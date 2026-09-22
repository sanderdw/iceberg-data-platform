"""Polaris adapter. Teams and memberships persist in Polaris/PostgreSQL.

Mutations are serialized by the API. Reversible multi-provider changes compensate
in reverse order. Deletion keeps its catalog as a durable retry marker until last.
"""

import json
import secrets
import time
from contextlib import contextmanager
from datetime import UTC, datetime
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
# One grant per shared object and nothing on the namespace or catalog: Polaris does
# not filter listings, so any list privilege would reveal every other name.
SHARE_PRIVILEGES = {"table": "TABLE_READ_DATA", "view": "VIEW_READ_PROPERTIES"}
MAX_SHARES = 20


def enc(value):
    return quote(value, safe="")


def catalog_role(role):
    return "admin" if role == "bucket-admin" else role


def grants(access):
    return {(id, catalog_role(d["role"])) for id, d in access.items()}


def share_grant(o):
    return {
        "type": o["kind"],
        "namespace": o["namespace"],
        f"{o['kind']}Name": o["name"],
        "privilege": SHARE_PRIVILEGES[o["kind"]],
    }


def grant_key(grant):
    return json.dumps(grant, sort_keys=True)


def buckets(access):
    return sorted(d["bucket"] for d in access.values() if d["role"] == "bucket-admin")


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
        if response.status_code == 400 and (method == "DELETE" or path.endswith("/grants?cascade=false")):
            # Polaris reports an already-absent grant as 400, not 404. Treat
            # only that explicit condition as successful idempotent removal.
            try:
                if response.json().get("error", {}).get("type") == "GRANT_NOT_FOUND":
                    return None
            except (ValueError, AttributeError):
                pass
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

    def update_properties(self, path, properties, *, expected_version=None):
        current = self.management(path)
        if expected_version is not None and current["entityVersion"] != expected_version:
            raise ServiceError(409, "This database changed. Refresh and try again.")
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

    def is_share(self, resource):
        return self.managed(resource) and resource["properties"].get("portal.kind") == "share"

    def is_user(self, resource):
        # Share principals are machine identities: never a user, member or portal login.
        return self.managed(resource) and not self.is_share(resource)

    def require_user(self, path):
        principal = self.require(path)
        if not self.is_user(principal):
            raise ServiceError(404, "Not found.")
        return principal

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
                roles = {m["team"]: m["role"] for m in user["memberships"]}
                self.update_memberships(user["id"], {t: r for t, r in roles.items() if t != id})
                undo.append(lambda u=user, r=roles: self.update_memberships(u["id"], r))
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

    @staticmethod
    def memberships(properties):
        # Principals written before per-team roles carry one role for all their teams.
        if "portal.memberships" in properties:
            return dict(json.loads(properties["portal.memberships"]))
        return {team: properties["portal.role"] for team in json.loads(properties["portal.teams"])}

    @staticmethod
    def membership_properties(properties, roles):
        # The legacy keys are dropped, not dual-written: an older portal would
        # apply a stale single role to every team.
        kept = {k: v for k, v in properties.items() if k not in ("portal.teams", "portal.role")}
        return {**kept, "portal.memberships": json.dumps(roles)}

    def user(self, principal):
        p = principal["properties"]
        roles = self.memberships(p)
        return {
            "id": principal["name"],
            "name": p["portal.name"],
            "memberships": [{"team": team, "role": role} for team, role in roles.items()],
            "teams": list(roles),
            "bucketAccess": "bucket-admin" in roles.values() and bool(p.get("portal.bucket-access-key")),
            "clientId": principal.get("clientId"),
            "createdAt": principal.get("createTimestamp"),
        }

    def list_users(self):
        return [self.user(p) for p in self.management("/principals")["principals"] if self.is_user(p)]

    def access(self, user, databases):
        roles = {m["team"]: m["role"] for m in user["memberships"]}
        return {
            d["id"]: {**d, "role": roles[d["team"]]}
            for d in databases
            if d["team"] in roles and d["status"] == "ready"
        }

    def sync_access(self, user, before, after, undo, *, s3=True):
        base = f"/principal-roles/{enc(user['id'])}/catalog-roles"
        # Revoke first, then grant. No temporary union of old and new access,
        # also when a database stays in scope and only its role changes.
        for db, role in sorted(grants(before) - grants(after)):
            path = f"{base}/{enc(db)}"
            self.remove(f"{path}/{role}")
            undo.append(lambda p=path, r=role: self.management(p, "PUT", {"catalogRole": {"name": r}}))
        if s3 and buckets(before) != buckets(after):
            principal = self.require(f"/principals/{enc(user['id'])}")
            if key := principal["properties"].get("portal.bucket-access-key"):
                self.storage.set_user_buckets(key, buckets(after))
                undo.append(lambda: self.storage.set_user_buckets(key, buckets(before)))
        for db, role in sorted(grants(after) - grants(before)):
            path = f"{base}/{enc(db)}"
            self.management(path, "PUT", {"catalogRole": {"name": role}})
            undo.append(lambda p=path, r=role: self.remove(f"{p}/{r}"))

    def sync_team_shares(self, user, before, after, undo, *, shares=None):
        """Use personal roles so password and Keycloak identities share the same access."""
        for share in self.list_shares() if shares is None else shares:
            team = share.get("recipientTeam")
            if not team or (team in before) == (team in after):
                continue
            base = f"/principal-roles/{enc(user['id'])}/catalog-roles/{enc(share['database'])}"
            role = share["id"]
            if team in after:
                self.management(base, "PUT", {"catalogRole": {"name": role}})
                undo.append(lambda b=base, r=role: self.remove(f"{b}/{r}"))
            else:
                self.remove(f"{base}/{role}")
                undo.append(lambda b=base, r=role: self.management(b, "PUT", {"catalogRole": {"name": r}}))

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
        # Publish move intent before checking shares. Creators publish their inactive
        # principal before rechecking this marker and epoch, so one side must refuse.
        # Keep the epoch even after failure to fence creators spanning an entire move.
        properties = {**catalog["properties"], "portal.share-epoch": uuid4().hex}
        properties.pop("portal.moving", None)
        with provision() as undo:
            self.update_properties(path, {**properties, "portal.moving": team}, expected_version=catalog["entityVersion"])
            undo.append(lambda: self.update_properties(path, properties))
            if self.list_shares(id):
                raise ServiceError(409, "Revoke this database's data shares first.")
            for user in self.list_users():
                # Also re-binds when the user holds different roles in both teams.
                self.sync_access(user, self.access(user, databases), self.access(user, moved), undo)
            bucket = catalog["properties"]["portal.bucket"]
            self.storage.tag_bucket(bucket, id, team)
            undo.append(lambda: self.storage.tag_bucket(bucket, id, old))
            result = self.update_properties(path, {**properties, "portal.team": team})
            return self.database(result)

    def rename_database(self, id, name, *, expected_team=None, expected_environment=None):
        path = f"/catalogs/{enc(id)}"
        catalog = self.require(path)
        if not self.managed(catalog):
            raise ServiceError(404, "Database not found.")
        properties = catalog["properties"]
        if expected_team is not None and properties["portal.team"] != expected_team:
            raise ServiceError(409, "This database changed teams. Refresh and try again.")
        if expected_environment is not None and properties["portal.environment"] != expected_environment:
            raise ServiceError(409, "This database changed environments. Refresh and try again.")
        if properties.get("portal.deleting") == "true":
            raise ServiceError(409, "This database is being deleted.")
        if properties.get("portal.moving"):
            raise ServiceError(409, "This database is being moved. Try again after the move completes.")
        if name == properties["portal.name"]:
            return self.database(catalog)
        if any(
            d["id"] != id and d["name"] == name
            and d["team"] == properties["portal.team"]
            and d["environment"] == properties["portal.environment"]
            for d in self.list_databases()
        ):
            raise ServiceError(409, "This database name already exists in this team and environment.")
        return self.database(self.update_properties(
            path, {**properties, "portal.name": name}, expected_version=catalog["entityVersion"],
        ))

    def create_user(self, data, *, identity_properties=None, activate=True):
        roles = data.roles
        self.require_teams(list(roles))
        if any(u["name"] == data.name for u in self.list_users()):
            raise ServiceError(409, "This username already exists.")
        id = f"portal-{uuid4().hex}"
        path, role_path = f"/principals/{id}", f"/principal-roles/{id}"
        key = f"portal{secrets.token_hex(12)}" if "bucket-admin" in roles.values() else None
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
                            "portal.memberships": json.dumps(roles),
                            **({"portal.bucket-access-key": key} if key else {}),
                            **(identity_properties or {}),
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
                bucket_credentials = self.storage.create_user(key, buckets(access))
                undo.append(lambda: self.storage.delete_user(key))
            # S3 policy was just created. Only grant catalog permissions here.
            self.sync_access(user, {}, access, undo, s3=False)
            self.sync_team_shares(user, set(), set(roles), undo)
            if activate:
                self.management(f"{path}/principal-roles", "PUT", {"principalRole": {"name": id}})
            return {
                "user": user,
                "credentials": result["credentials"],
                **({"bucketCredentials": bucket_credentials} if bucket_credentials else {}),
            }

    def update_memberships(self, id, roles):
        self.require_teams(list(roles))
        path = f"/principals/{enc(id)}"
        principal = self.require_user(path)
        user = self.user(principal)
        databases = self.list_databases()
        before = self.access(user, databases)
        memberships = [{"team": team, "role": role} for team, role in roles.items()]
        after = self.access({**user, "memberships": memberships}, databases)
        properties = self.membership_properties(principal["properties"], roles)
        credentials = None
        with provision() as undo:
            if "bucket-admin" in roles.values() and not properties.get("portal.bucket-access-key"):
                key = f"portal{secrets.token_hex(12)}"
                credentials = self.storage.create_user(key, buckets(after))
                undo.append(lambda: self.storage.delete_user(key))
                properties["portal.bucket-access-key"] = key
                self.sync_access(user, before, after, undo, s3=False)
            else:
                # An existing key is retained with a deny-all policy once the last
                # bucket-admin membership goes. This preserves credentials for
                # re-promotion and lets failed changes roll back.
                self.sync_access(user, before, after, undo)
            self.sync_team_shares(user, set(user["teams"]), set(roles), undo)
            updated = self.update_properties(path, properties)
            return {
                "user": self.user(updated),
                **({"bucketCredentials": credentials} if credentials else {}),
            }

    def delete_user(self, id):
        path = f"/principals/{enc(id)}"
        principal = self.require_user(path)
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

    def delete_database(self, id, *, expected_team=None, expected_environment=None):
        path = f"/catalogs/{enc(id)}"
        catalog = self.require(path)
        if not self.managed(catalog):
            raise ServiceError(404, "Database not found.")
        if expected_team is not None and catalog["properties"]["portal.team"] != expected_team:
            raise ServiceError(409, "This database changed teams. Refresh and try again.")
        if expected_environment is not None and catalog["properties"]["portal.environment"] != expected_environment:
            raise ServiceError(409, "This database changed environments. Refresh and try again.")
        if catalog["properties"].get("portal.moving"):
            raise ServiceError(409, "This database is being moved. Try again after the move completes.")
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
                expected_version=catalog["entityVersion"],
            )
        # External access ends with the database, before anything else is taken apart.
        for principal in self.management("/principals")["principals"]:
            if self.is_share(principal) and principal["properties"]["portal.database"] == id:
                self.delete_share(principal["name"])
        databases = self.list_databases()
        for user in self.list_users():
            if catalog["properties"]["portal.team"] in user["teams"]:
                # Every role: the membership role may have changed since a failed attempt.
                for role in ROLES:
                    self.remove(f"/principal-roles/{enc(user['id'])}/catalog-roles/{enc(id)}/{role}")
                if user["bucketAccess"]:
                    principal = self.require(f"/principals/{enc(user['id'])}")
                    self.storage.set_user_buckets(
                        principal["properties"]["portal.bucket-access-key"],
                        buckets(self.access(user, databases)),
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
        # The user gateway is a second writer: a share created there during this deletion
        # leaves a role that would block the catalog drop. Its principal is revoked as an orphan.
        for role in self.management(f"{path}/catalog-roles")["roles"]:
            if role["name"].startswith("share-"):
                self.remove(f"{path}/catalog-roles/{enc(role['name'])}")
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

    def share(self, principal, actual=None):
        p = principal["properties"]
        objects = json.loads(p["portal.objects"])
        result = {
            "id": principal["name"],
            "name": p["portal.name"],
            "recipient": p.get("portal.recipient", ""),
            "external": p.get("portal.external", "true") == "true",
            "recipientTeam": p.get("portal.recipient-team"),
            "description": p.get("portal.description", ""),
            "database": p["portal.database"],
            "objects": objects,
            "expiresAt": p.get("portal.expires-at"),
            "createdAt": principal.get("createTimestamp"),
            "createdBy": p.get("portal.created-by-name", ""),
            "clientId": principal.get("clientId"),
            "status": "revoking" if p.get("portal.deleting") == "true" else "active",
        }
        if actual is not None:
            # Polaris keys grants by entity, not name: a renamed object stays granted
            # under its new name and a recreated one is not. Show what is really granted.
            wanted = {grant_key(share_grant(o)) for o in objects}
            granted = {grant_key(g) for g in actual}
            result["objects"] = [{**o, "granted": grant_key(share_grant(o)) in granted} for o in objects]
            result["extraGrants"] = [
                {
                    "kind": g.get("type"),
                    "namespace": g.get("namespace", []),
                    "name": g.get("tableName") or g.get("viewName") or "",
                    "privilege": g["privilege"],
                }
                for g in actual
                if grant_key(g) not in wanted
            ]
        return result

    def share_role(self, principal):
        database = principal["properties"]["portal.database"]
        return f"/catalogs/{enc(database)}/catalog-roles/{enc(principal['name'])}"

    def share_principals(self):
        """Live shares. Expired, orphaned and half-revoked shares are revoked first.

        Expiry is enforced here, not in a user interface. The other portal process can
        delete a database concurrently; its shares then surface here as orphans.
        """
        now = datetime.now(UTC)
        databases = {c["name"] for c in self.management("/catalogs")["catalogs"] if self.managed(c)}
        live = []
        for principal in self.management("/principals")["principals"]:
            if not self.is_share(principal):
                continue
            p = principal["properties"]
            expires = p.get("portal.expires-at")
            if (
                p.get("portal.deleting") == "true"
                or p["portal.database"] not in databases
                or (expires and datetime.fromisoformat(expires) <= now)
            ):
                self.delete_share(principal["name"])
            else:
                live.append(principal)
        return live

    def expire_shares(self):
        self.share_principals()

    def list_shares(self, database=None, *, drift=False):
        shares = []
        for principal in self.share_principals():
            if database and principal["properties"]["portal.database"] != database:
                continue
            actual = self.management(f"{self.share_role(principal)}/grants")["grants"] if drift else None
            shares.append(self.share(principal, actual))
        if any(s.get("recipientTeam") for s in shares):
            teams = {t["id"]: t["name"] for t in self.list_teams()}
            for share in shares:
                if share.get("recipientTeam"):
                    share["recipientTeamName"] = teams.get(share["recipientTeam"], "Deleted team")
        return sorted(shares, key=lambda s: (s["database"], s["name"]))

    def require_share(self, id):
        principal = self.require(f"/principals/{enc(id)}")
        if not self.is_share(principal):
            raise ServiceError(404, "Not found.")
        return principal

    def editable_share(self, id):
        principal = self.require_share(id)
        if principal["properties"].get("portal.deleting") == "true":
            raise ServiceError(409, "This share is being revoked.")
        return principal

    def grant_share(self, role_path, grant):
        try:
            self.management(f"{role_path}/grants", "PUT", {"grant": grant})
        except ServiceError as exc:
            if exc.status == 404:
                raise ServiceError(404, "A selected table or view no longer exists. Refresh and try again.") from exc
            raise

    def revoke_share_grant(self, role_path, grant):
        try:
            self.management(f"{role_path}/grants?cascade=false", "POST", {"grant": grant})
        except ServiceError as exc:
            # The object was dropped in the meantime; Polaris removed its grants with it.
            if exc.status != 404:
                raise

    def reconcile_share(self, role_path, objects, undo):
        desired = {grant_key(g): g for g in map(share_grant, objects)}
        actual = {grant_key(g): g for g in self.management(f"{role_path}/grants")["grants"]}
        # Revoke first, then grant, against what Polaris really holds.
        for key in sorted(actual.keys() - desired.keys()):
            self.revoke_share_grant(role_path, actual[key])
            undo.append(lambda g=actual[key]: self.grant_share(role_path, g))
        for key in sorted(desired.keys() - actual.keys()):
            self.grant_share(role_path, desired[key])
            undo.append(lambda g=desired[key]: self.revoke_share_grant(role_path, g))

    def share_connection(self, principal, credentials):
        p = principal["properties"]
        connection = self.connection(p["portal.database"])
        return {
            "type": connection["type"],
            "uri": connection["uri"],
            "warehouse": connection["warehouse"],
            "oauth2ServerUri": connection["oauth2ServerUri"],
            "scope": connection["scope"],
            "accessDelegation": "vended-credentials",
            "s3Endpoint": connection["s3Endpoint"],
            "credential": f"{credentials['clientId']}:{credentials['clientSecret']}",
            # The credential cannot list namespaces or tables; these names are the contract.
            "identifiers": [
                {"kind": o["kind"], "identifier": ".".join([*o["namespace"], o["name"]])}
                for o in json.loads(p["portal.objects"])
            ],
        }

    def issued(self, principal, credentials):
        # The secret leaves Polaris here only; it is never stored by the portal.
        return {
            "share": self.share(principal),
            "credentials": credentials,
            "connection": self.share_connection(principal, credentials),
        }

    def create_share(self, data, created_by, *, expected_team=None):
        catalog = self.require(f"/catalogs/{enc(data.database)}")
        if catalog["properties"].get("portal.deleting") == "true":
            raise ServiceError(409, "This database is being deleted.")
        if catalog["properties"].get("portal.moving"):
            raise ServiceError(409, "This database is being moved. Try again after the move completes.")
        if expected_team is not None and catalog["properties"]["portal.team"] != expected_team:
            raise ServiceError(409, "This database changed teams. Refresh and try again.")
        if data.recipient_team:
            self.require_teams([data.recipient_team])
            if data.recipient_team == catalog["properties"]["portal.team"]:
                raise ServiceError(422, "Choose a team other than the database owner.")
        existing = self.list_shares(data.database)
        if len(existing) >= MAX_SHARES:
            raise ServiceError(409, f"A database can have at most {MAX_SHARES} data shares.")
        if any(s["name"] == data.name for s in existing):
            raise ServiceError(409, "This share name already exists in this database.")
        id = f"share-{uuid4().hex}"
        roles = f"/catalogs/{enc(data.database)}/catalog-roles"
        marker = {"portal.managed-by": MANAGED, "portal.kind": "share"}
        objects = [o.model_dump() for o in data.objects]
        with provision() as undo:
            self.management(roles, "POST", {"catalogRole": {"name": id, "properties": marker}})
            # Removing the catalog role removes its grants.
            undo.append(lambda: self.remove(f"{roles}/{id}"))
            for o in objects:
                self.grant_share(f"{roles}/{id}", share_grant(o))
            self.management("/principal-roles", "POST", {"principalRole": {"name": id, "properties": marker}})
            undo.append(lambda: self.remove(f"/principal-roles/{id}"))
            self.management(
                f"/principal-roles/{id}/catalog-roles/{enc(data.database)}", "PUT", {"catalogRole": {"name": id}}
            )
            result = self.management(
                "/principals",
                "POST",
                {
                    "principal": {
                        "name": id,
                        "properties": {
                            **marker,
                            "portal.name": data.name,
                            "portal.recipient": data.recipient,
                            "portal.external": str(data.external).lower(),
                            **({"portal.recipient-team": data.recipient_team} if data.recipient_team else {}),
                            "portal.description": data.description,
                            "portal.database": data.database,
                            "portal.objects": json.dumps(objects),
                            **({"portal.expires-at": data.expires_at.isoformat()} if data.expires_at else {}),
                            "portal.created-by": created_by["id"],
                            "portal.created-by-name": created_by["name"],
                        },
                    }
                },
            )
            undo.append(lambda: self.remove(f"/principals/{id}"))
            # The principal is now visible to a concurrent move's share preflight,
            # but its credential still has no access. Reject stale authorization,
            # including a move away and back to the same team during creation.
            current = self.require(f"/catalogs/{enc(data.database)}")["properties"]
            if (
                current.get("portal.moving")
                or current.get("portal.deleting") == "true"
                or current["portal.team"] != catalog["properties"]["portal.team"]
                or current.get("portal.share-epoch") != catalog["properties"].get("portal.share-epoch")
            ):
                raise ServiceError(409, "This database changed during share creation. Refresh and try again.")
            # Activation last: until here the new secret opens nothing.
            if data.external:
                self.management(f"/principals/{id}/principal-roles", "PUT", {"principalRole": {"name": id}})
            if data.recipient_team:
                for user in self.list_users():
                    if data.recipient_team in user["teams"]:
                        self.sync_team_shares(
                            user, set(), set(user["teams"]), undo,
                            shares=[self.share(result["principal"])],
                        )
            if not data.external:
                return {"share": self.share(result["principal"])}
            return self.issued(result["principal"], result["credentials"])

    def update_share(self, id, data):
        principal = self.editable_share(id)
        properties = dict(principal["properties"])
        changed = data.model_fields_set
        if "recipient" in changed and data.recipient is not None:
            properties["portal.recipient"] = data.recipient
        if "description" in changed and data.description is not None:
            properties["portal.description"] = data.description
        if "expires_at" in changed:
            properties.pop("portal.expires-at", None)
            if data.expires_at:
                properties["portal.expires-at"] = data.expires_at.isoformat()
        with provision() as undo:
            # Saving the selection also re-grants objects that were dropped and recreated.
            if data.objects is not None:
                objects = [o.model_dump() for o in data.objects]
                self.reconcile_share(self.share_role(principal), objects, undo)
                properties["portal.objects"] = json.dumps(objects)
            updated = self.update_properties(f"/principals/{enc(id)}", properties)
        actual = self.management(f"{self.share_role(principal)}/grants")["grants"]
        return self.share(updated, actual)

    def rotate_share(self, id):
        principal = self.editable_share(id)
        if principal["properties"].get("portal.external", "true") != "true":
            raise ServiceError(409, "This share is only available to a team and has no external credential.")
        # `rotate` is reserved for the principal itself; `reset` keeps the client ID
        # and invalidates the old secret at once.
        result = self.management(f"/principals/{enc(id)}/reset", "POST", {})
        return self.issued(principal, result["credentials"])

    def delete_share(self, id):
        path = f"/principals/{enc(id)}"
        principal = self.require_share(id)
        # Persist intent first. The principal is the retry marker and goes last;
        # `share_principals` resumes a half-finished revocation.
        if principal["properties"].get("portal.deleting") != "true":
            self.update_properties(path, {**principal["properties"], "portal.deleting": "true"})
        # Stop external access, then remove recipient members’ catalog grants.
        self.remove(f"{path}/principal-roles/{enc(id)}")
        if team := principal["properties"].get("portal.recipient-team"):
            for user in self.list_users():
                if team in user["teams"]:
                    self.remove(
                        f"/principal-roles/{enc(user['id'])}/catalog-roles/"
                        f"{enc(principal['properties']['portal.database'])}/{enc(id)}"
                    )
        self.remove(self.share_role(principal))
        self.remove(f"/principal-roles/{enc(id)}")
        self.remove(path)
