"""Polaris adapter. Teams and memberships persist in Polaris/PostgreSQL.

Mutations are serialized by the API. Reversible multi-provider changes compensate
in reverse order. Deletion keeps its catalog as a durable retry marker until last.
"""

import json
import secrets
import time
from contextlib import contextmanager
from urllib.parse import quote
from uuid import uuid4

import httpx

from .models import ServiceError

MANAGED = "iceberg-portal-v2"
ROLES = {
    "reader": [
        "CATALOG_READ_PROPERTIES",
        "NAMESPACE_LIST",
        "NAMESPACE_READ_PROPERTIES",
        "TABLE_LIST",
        "TABLE_READ_PROPERTIES",
        "TABLE_READ_DATA",
        "VIEW_LIST",
        "VIEW_READ_PROPERTIES",
    ],
    "writer": ["CATALOG_MANAGE_CONTENT"],
    "admin": ["CATALOG_MANAGE_CONTENT", "CATALOG_MANAGE_ACCESS", "CATALOG_MANAGE_METADATA"],
}


def enc(value):
    return quote(value, safe="")


@contextmanager
def provision():
    undo = []
    try:
        yield undo
    except Exception:
        failed = False
        for cleanup in reversed(undo):
            try:
                cleanup()
            except Exception:
                failed = True
        if failed:
            raise ServiceError(
                502,
                "The action partially failed and cleanup was incomplete. "
                "Ask the administrator to check Polaris and RustFS.",
            ) from None
        raise


class PolarisProvider:
    def __init__(self, env, storage):
        self.url = env.get("POLARIS_URL", "http://localhost:8181")
        self.public_url = env.get("POLARIS_PUBLIC_URL", "http://localhost:8181")
        self.env = env
        self.storage = storage
        self.http = httpx.Client(timeout=15)
        self.token = None
        self.token_until = 0

    def close(self):
        self.http.close()
        self.storage.close()

    def access_token(self):
        if self.token and self.token_until > time.monotonic():
            return self.token
        response = self.http.post(
            f"{self.url}/api/catalog/v1/oauth/tokens",
            data={
                "grant_type": "client_credentials",
                "client_id": self.env.get("POLARIS_CLIENT_ID", ""),
                "client_secret": self.env.get("POLARIS_CLIENT_SECRET", ""),
                "scope": "PRINCIPAL_ROLE:ALL",
            },
            headers={"Polaris-Realm": "POLARIS"},
        )
        if response.is_error:
            raise ServiceError(
                502, "Authentication with the data provider failed. Check the server configuration."
            )
        data = response.json()
        self.token = data["access_token"]
        self.token_until = time.monotonic() + max(0, data["expires_in"] - 30)
        return self.token

    def request(self, path, method="GET", body=None, retry=True):
        try:
            response = self.http.request(
                method,
                self.url + path,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Polaris-Realm": "POLARIS",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The data provider is unavailable. Check the local stack.") from exc
        if response.status_code == 401 and retry:
            self.token = None
            return self.request(path, method, body, False)
        if response.is_error:
            status = response.status_code if response.status_code in (404, 409) else 502
            message = {
                404: "Not found.",
                409: "This name already exists or the data has changed. Refresh the page.",
            }
            raise ServiceError(
                status,
                message.get(
                    status, f"The data provider could not perform this action (HTTP {response.status_code})."
                ),
            )
        return response.json() if response.content else None

    def management(self, path, method="GET", body=None):
        return self.request(f"/api/management/v1{path}", method, body)

    def remove(self, path):
        try:
            self.management(path, "DELETE")
        except ServiceError as exc:
            if exc.status != 404:
                raise

    def update_properties(self, path, properties):
        current = self.management(path)
        return self.management(
            path, "PUT", {"currentEntityVersion": current["entityVersion"], "properties": properties}
        )

    def health(self):
        try:
            self.management("/catalogs")
            status = "online"
        except ServiceError:
            status = "offline"
        return {
            "provider": "Apache Polaris",
            "status": status,
            "storage": "RustFS / S3",
            "capabilities": ["teams", "databases", "users", "roles", "connections"],
        }

    def managed(self, resource):
        return resource.get("properties", {}).get("portal.managed-by") == MANAGED

    def require(self, path):
        resource = self.management(path)
        if not self.managed(resource):
            raise ServiceError(404, "Not found.")
        return resource

    def team(self, role):
        return {
            "id": role["name"],
            "name": role["properties"]["portal.name"],
            "description": role["properties"].get("portal.description", ""),
            "createdAt": role.get("createTimestamp"),
        }

    def list_teams(self):
        return sorted(
            [
                self.team(r)
                for r in self.management("/principal-roles")["roles"]
                if self.managed(r) and r["properties"].get("portal.kind") == "team"
            ],
            key=lambda t: t["name"],
        )

    def require_teams(self, ids):
        if not ids or len(ids) != len(set(ids)):
            raise ServiceError(400, "A user must belong to at least one team; teams must be unique.")
        available = {t["id"] for t in self.list_teams()}
        if not set(ids) <= available:
            raise ServiceError(404, "A selected team no longer exists.")

    def save_team(self, data, id=None):
        existing = self.list_teams()
        if id and id not in {t["id"] for t in existing}:
            raise ServiceError(404, "Team not found.")
        if any(t["name"] == data.name and t["id"] != id for t in existing):
            raise ServiceError(409, "This team name already exists.")
        props = {
            "portal.managed-by": MANAGED,
            "portal.kind": "team",
            "portal.name": data.name,
            "portal.description": data.description,
        }
        if id:
            result = self.update_properties(f"/principal-roles/{enc(id)}", props)
        else:
            result = self.management(
                "/principal-roles",
                "POST",
                {"principalRole": {"name": f"team-{uuid4().hex}", "properties": props}},
            )
        return self.team(result)

    def delete_team(self, id):
        self.require_teams([id])
        if any(d["team"] == id for d in self.list_databases()):
            raise ServiceError(409, "Move or delete this team's databases first.")
        users = [u for u in self.list_users() if id in u["teams"]]
        if any(len(u["teams"]) == 1 for u in users):
            raise ServiceError(409, "This is a user's last team. Assign that user to another team first.")
        with provision() as undo:
            for user in users:
                self.update_memberships(user["id"], [t for t in user["teams"] if t != id])
                undo.append(lambda u=user: self.update_memberships(u["id"], u["teams"]))
            self.remove(f"/principal-roles/{enc(id)}")

    def database(self, catalog):
        p = catalog["properties"]
        return {
            "id": catalog["name"],
            "name": p["portal.name"],
            "team": p["portal.team"],
            "description": p.get("portal.description", ""),
            "environment": p["portal.environment"],
            "bucket": p["portal.bucket"],
            "storageLocation": p["default-base-location"],
            "createdAt": catalog.get("createTimestamp"),
            "engine": "Apache Iceberg",
            "status": "deleting" if p.get("portal.deleting") == "true" else "ready",
        }

    def list_databases(self):
        return [self.database(c) for c in self.management("/catalogs")["catalogs"] if self.managed(c)]

    def explorer_databases(self):
        """All catalogs, including catalogs created outside the portal; no raw properties."""
        return sorted(
            [
                {
                    "id": c["name"],
                    "name": c.get("properties", {}).get("portal.name", c["name"]),
                    "type": c.get("type", "INTERNAL"),
                    "managed": self.managed(c),
                    "team": c.get("properties", {}).get("portal.team"),
                    "environment": c.get("properties", {}).get("portal.environment"),
                    "status": "deleting"
                    if c.get("properties", {}).get("portal.deleting") == "true"
                    else "ready",
                }
                for c in self.management("/catalogs")["catalogs"]
            ],
            key=lambda c: c["name"],
        )

    @contextmanager
    def explorer_access(self, database):
        """Temporary metadata-listing rights for the portal's service-admin identity only.

        Deleting the unique catalog role removes its grants and all assignments.
        Existing roles and team permissions are never altered.
        """
        path = f"/catalogs/{enc(database)}"
        catalog = self.management(path)
        if catalog.get("properties", {}).get("portal.deleting") == "true":
            raise ServiceError(409, "This database is being deleted.")
        role = f"portal-explorer-{uuid4().hex}"
        role_path = f"{path}/catalog-roles/{role}"
        self.management(f"{path}/catalog-roles", "POST", {"catalogRole": {"name": role}})
        try:
            for privilege in ("CATALOG_READ_PROPERTIES", "NAMESPACE_LIST", "TABLE_LIST", "VIEW_LIST"):
                self.management(
                    f"{role_path}/grants", "PUT", {"grant": {"type": "catalog", "privilege": privilege}}
                )
            self.management(
                f"/principal-roles/service_admin/catalog-roles/{enc(database)}",
                "PUT",
                {"catalogRole": {"name": role}},
            )
            yield
        finally:
            try:
                self.remove(role_path)
            except ServiceError as exc:
                raise ServiceError(
                    502,
                    "The temporary read role could not be removed. "
                    "Ask the portal administrator to check the portal-explorer roles in Polaris.",
                ) from exc

    def explorer_contents(self, database, namespace):
        prefix = f"/api/catalog/v1/{enc(database)}"
        encoded = enc("\x1f".join(namespace))
        with self.explorer_access(database):
            children = self.pages(
                f"{prefix}/namespaces" + (f"?parent={encoded}" if namespace else ""), "namespaces"
            )
            tables, views = [], []
            if namespace:
                path = f"{prefix}/namespaces/{encoded}"
                tables = self.pages(f"{path}/tables", "identifiers")
                views = self.pages(f"{path}/views", "identifiers")
        # Only names and namespace identifiers leave the backend, never properties,
        # table metadata, view SQL or vended storage credentials.
        return {
            "database": database,
            "namespace": namespace,
            "namespaces": sorted(children),
            "tables": sorted(
                [{"name": t["name"], "namespace": t["namespace"]} for t in tables], key=lambda t: t["name"]
            ),
            "views": sorted(
                [{"name": v["name"], "namespace": v["namespace"]} for v in views], key=lambda v: v["name"]
            ),
        }

    def user(self, principal):
        p = principal["properties"]
        return {
            "id": principal["name"],
            "name": p["portal.name"],
            "teams": json.loads(p["portal.teams"]),
            "role": p["portal.role"],
            "bucketAccess": p["portal.role"] == "bucket-admin" and bool(p.get("portal.bucket-access-key")),
            "clientId": principal.get("clientId"),
            "createdAt": principal.get("createTimestamp"),
        }

    def list_users(self):
        return [self.user(p) for p in self.management("/principals")["principals"] if self.managed(p)]

    def access(self, user, databases):
        return {d["id"]: d for d in databases if d["team"] in user["teams"] and d["status"] == "ready"}

    def sync_access(self, user, before, after, undo):
        role = "admin" if user["role"] == "bucket-admin" else user["role"]
        base = f"/principal-roles/{enc(user['id'])}/catalog-roles"
        # Revoke first, then grant. No temporary union of old and new access.
        for db in before.keys() - after.keys():
            path = f"{base}/{enc(db)}"
            self.remove(f"{path}/{role}")
            undo.append(lambda p=path: self.management(p, "PUT", {"catalogRole": {"name": role}}))
        if user["bucketAccess"]:
            principal = self.require(f"/principals/{enc(user['id'])}")
            key = principal["properties"]["portal.bucket-access-key"]
            self.storage.set_user_buckets(key, [d["bucket"] for d in after.values()])
            undo.append(lambda: self.storage.set_user_buckets(key, [d["bucket"] for d in before.values()]))
        for db in after.keys() - before.keys():
            path = f"{base}/{enc(db)}"
            self.management(path, "PUT", {"catalogRole": {"name": role}})
            undo.append(lambda p=path: self.remove(f"{p}/{role}"))

    def create_database(self, data):
        self.require_teams([data.team])
        if any(
            d["name"] == data.name and d["team"] == data.team and d["environment"] == data.environment
            for d in self.list_databases()
        ):
            raise ServiceError(409, "This database name already exists in this team and environment.")
        id = f"db-{uuid4().hex}"
        path = f"/catalogs/{enc(id)}"
        with provision() as undo:
            bucket = self.storage.create_bucket(id, data.team)
            undo.append(lambda: self.storage.delete_empty_bucket(bucket))
            self.management(
                "/catalogs",
                "POST",
                {
                    "catalog": {
                        "name": id,
                        "type": "INTERNAL",
                        "properties": {
                            "default-base-location": f"s3://{bucket}/",
                            "portal.bucket": bucket,
                            "portal.managed-by": MANAGED,
                            "portal.team": data.team,
                            "portal.name": data.name,
                            "portal.environment": data.environment,
                            "portal.description": data.description,
                        },
                        "storageConfigInfo": {
                            "storageType": "S3",
                            "allowedLocations": [f"s3://{bucket}/"],
                            "endpoint": self.env.get("S3_ENDPOINT", "http://localhost:9000"),
                            "endpointInternal": self.env.get("S3_INTERNAL_ENDPOINT", "http://rustfs:9000"),
                            "pathStyleAccess": True,
                            "region": self.env.get("AWS_REGION", "us-east-1"),
                        },
                    }
                },
            )
            undo.append(lambda: self.remove(path))
            for role, privileges in ROLES.items():
                role_path = f"{path}/catalog-roles/{role}"
                self.management(f"{path}/catalog-roles", "POST", {"catalogRole": {"name": role}})
                undo.append(lambda p=role_path: self.remove(p))
                for privilege in privileges:
                    self.management(
                        f"{role_path}/grants", "PUT", {"grant": {"type": "catalog", "privilege": privilege}}
                    )
            databases = self.list_databases()
            for user in self.list_users():
                after = self.access(user, databases)
                before = {k: v for k, v in after.items() if k != id}
                if before != after:
                    self.sync_access(user, before, after, undo)
            return self.database(self.management(path))

    def move_database(self, id, team):
        self.require_teams([team])
        path = f"/catalogs/{enc(id)}"
        catalog = self.require(path)
        old = catalog["properties"]["portal.team"]
        if catalog["properties"].get("portal.deleting") == "true":
            raise ServiceError(409, "This database is being deleted. Complete the deletion first.")
        if old == team:
            return self.database(catalog)
        databases = self.list_databases()
        if any(
            d["team"] == team
            and d["name"] == catalog["properties"]["portal.name"]
            and d["environment"] == catalog["properties"]["portal.environment"]
            for d in databases
        ):
            raise ServiceError(
                409, "This database name already exists in the destination team and environment."
            )
        moved = [{**d, "team": team} if d["id"] == id else d for d in databases]
        with provision() as undo:
            for user in self.list_users():
                before, after = self.access(user, databases), self.access(user, moved)
                if before.keys() != after.keys():
                    self.sync_access(user, before, after, undo)
            bucket = catalog["properties"]["portal.bucket"]
            self.storage.tag_bucket(bucket, id, team)
            undo.append(lambda: self.storage.tag_bucket(bucket, id, old))
            result = self.update_properties(path, {**catalog["properties"], "portal.team": team})
            return self.database(result)

    def create_user(self, data):
        self.require_teams(data.teams)
        if any(u["name"] == data.name for u in self.list_users()):
            raise ServiceError(409, "This username already exists.")
        id = f"portal-{uuid4().hex}"
        path, role_path = f"/principals/{id}", f"/principal-roles/{id}"
        key = f"portal{secrets.token_hex(12)}" if data.role == "bucket-admin" else None
        with provision() as undo:
            result = self.management(
                "/principals",
                "POST",
                {
                    "principal": {
                        "name": id,
                        "properties": {
                            "portal.managed-by": MANAGED,
                            "portal.name": data.name,
                            "portal.teams": json.dumps(data.teams),
                            "portal.role": data.role,
                            **({"portal.bucket-access-key": key} if key else {}),
                        },
                    }
                },
            )
            undo.append(lambda: self.remove(path))
            self.management("/principal-roles", "POST", {"principalRole": {"name": id}})
            undo.append(lambda: self.remove(role_path))
            user = self.user(result["principal"])
            access = self.access(user, self.list_databases())
            bucket_credentials = None
            if key:
                bucket_credentials = self.storage.create_user(key, [d["bucket"] for d in access.values()])
                undo.append(lambda: self.storage.delete_user(key))
            # S3 policy was just created. Only grant catalog permissions here.
            self.sync_access({**user, "bucketAccess": False}, {}, access, undo)
            self.management(f"{path}/principal-roles", "PUT", {"principalRole": {"name": id}})
            return {
                "user": user,
                "credentials": result["credentials"],
                **({"bucketCredentials": bucket_credentials} if bucket_credentials else {}),
            }

    def update_memberships(self, id, teams):
        self.require_teams(teams)
        path = f"/principals/{enc(id)}"
        principal = self.require(path)
        user = self.user(principal)
        databases = self.list_databases()
        with provision() as undo:
            self.sync_access(
                user, self.access(user, databases), self.access({**user, "teams": teams}, databases), undo
            )
            updated = self.update_properties(
                path, {**principal["properties"], "portal.teams": json.dumps(teams)}
            )
            return self.user(updated)

    def update_role(self, id, role):
        path = f"/principals/{enc(id)}"
        principal = self.require(path)
        user = self.user(principal)
        if user["role"] == role:
            return {"user": user}
        access = self.access(user, self.list_databases())
        properties = {**principal["properties"], "portal.role": role}
        key = properties.get("portal.bucket-access-key")
        credentials = None
        with provision() as undo:
            old_catalog_role = "admin" if user["role"] == "bucket-admin" else user["role"]
            new_catalog_role = "admin" if role == "bucket-admin" else role
            if old_catalog_role != new_catalog_role:
                self.sync_access({**user, "bucketAccess": False}, access, {}, undo)
            buckets = [d["bucket"] for d in access.values()]
            if key:
                # Retain the key with a deny-all policy on demotion. This preserves
                # credentials for re-promotion and lets failed changes roll back.
                before = buckets if user["bucketAccess"] else []
                after = buckets if role == "bucket-admin" else []
                if before != after:
                    self.storage.set_user_buckets(key, after)
                    undo.append(lambda: self.storage.set_user_buckets(key, before))
            elif role == "bucket-admin":
                key = f"portal{secrets.token_hex(12)}"
                credentials = self.storage.create_user(key, buckets)
                undo.append(lambda: self.storage.delete_user(key))
                properties["portal.bucket-access-key"] = key
            if old_catalog_role != new_catalog_role:
                self.sync_access({**user, "role": role, "bucketAccess": False}, {}, access, undo)
            updated = self.update_properties(path, properties)
            return {
                "user": self.user(updated),
                **({"bucketCredentials": credentials} if credentials else {}),
            }

    def delete_user(self, id):
        path = f"/principals/{enc(id)}"
        principal = self.require(path)
        key = principal["properties"].get("portal.bucket-access-key")
        if key:
            self.storage.delete_user(key)
        # Deleting a principal role removes all associated catalog grants.
        self.remove(f"{path}/principal-roles/{enc(id)}")
        self.remove(f"/principal-roles/{enc(id)}")
        self.remove(path)

    def pages(self, path, key):
        values, token = [], None
        while True:
            query = f"pageToken={enc(token)}" if token else ""
            result = self.request(path + ("&" if "?" in path else "?") + query)
            values.extend(result.get(key, []))
            token = result.get("next-page-token")
            if not token:
                return values

    def clear_namespace(self, prefix, namespace):
        encoded = enc("\x1f".join(namespace))
        path = f"{prefix}/namespaces/{encoded}"
        for child in self.pages(f"{prefix}/namespaces?parent={encoded}", "namespaces"):
            self.clear_namespace(prefix, child)
        for kind in ("tables", "views"):
            for item in self.pages(f"{path}/{kind}", "identifiers"):
                self.request(f"{path}/{kind}/{enc(item['name'])}", "DELETE")
        self.request(path, "DELETE")

    def delete_database(self, id):
        path = f"/catalogs/{enc(id)}"
        catalog = self.require(path)
        # Persist intent first; on retry revoke again, even after partial failure.
        # Polaris drops views with purge, which must be enabled on this catalog.
        # This is restricted to a confirmed full database deletion.
        if (
            catalog["properties"].get("portal.deleting") != "true"
            or catalog["properties"].get("polaris.config.drop-with-purge.enabled") != "true"
        ):
            self.update_properties(
                path,
                {
                    **catalog["properties"],
                    "portal.deleting": "true",
                    "polaris.config.drop-with-purge.enabled": "true",
                },
            )
        databases = self.list_databases()
        for user in self.list_users():
            if catalog["properties"]["portal.team"] in user["teams"]:
                role = "admin" if user["role"] == "bucket-admin" else user["role"]
                self.remove(f"/principal-roles/{enc(user['id'])}/catalog-roles/{enc(id)}/{role}")
                if user["bucketAccess"]:
                    principal = self.require(f"/principals/{enc(user['id'])}")
                    self.storage.set_user_buckets(
                        principal["properties"]["portal.bucket-access-key"],
                        [d["bucket"] for d in self.access(user, databases).values()],
                    )
        # Grant the management identity content access for recursive Iceberg cleanup.
        self.management(
            f"{path}/catalog-roles/catalog_admin/grants",
            "PUT",
            {"grant": {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}},
        )
        self.management(
            f"/principal-roles/service_admin/catalog-roles/{enc(id)}",
            "PUT",
            {"catalogRole": {"name": "catalog_admin"}},
        )
        prefix = f"/api/catalog/v1/{enc(id)}"
        for namespace in self.pages(f"{prefix}/namespaces", "namespaces"):
            self.clear_namespace(prefix, namespace)
        self.storage.delete_bucket(catalog["properties"]["portal.bucket"])
        for role in ROLES:
            self.remove(f"{path}/catalog-roles/{role}")
        self.remove(f"/principal-roles/service_admin/catalog-roles/{enc(id)}/catalog_admin")
        self.remove(path)

    def connection(self, id):
        catalog = self.require(f"/catalogs/{enc(id)}")
        if catalog["properties"].get("portal.deleting") == "true":
            raise ServiceError(409, "This database is being deleted.")
        return {
            "type": "iceberg-rest",
            "uri": f"{self.public_url}/api/catalog",
            "warehouse": id,
            "oauth2ServerUri": f"{self.public_url}/api/catalog/v1/oauth/tokens",
            "scope": "PRINCIPAL_ROLE:ALL",
            "s3Endpoint": self.env.get("S3_ENDPOINT", "http://localhost:9000"),
            "bucket": catalog["properties"]["portal.bucket"],
            "storageLocation": catalog["properties"]["default-base-location"],
            "credential": "<client-id>:<client-secret>",
        }
