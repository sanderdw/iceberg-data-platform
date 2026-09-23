"""Trusted metadata directory plus catalog requests made as the actual user."""

import time
from dataclasses import dataclass, field

import httpx

from server.models import DatabaseInput, Environment, ServiceError
from server.polaris import PolarisProvider, enc
from server.storage import RustFSStorage

from .catalog import object_details, properties


def validate_namespace(parts):
    if len(parts) > 100 or any(
        not p or len(p) > 256 or p in (".", "..") or "\x1f" in p or "\0" in p for p in parts
    ):
        raise ServiceError(422, "Invalid namespace.")


@dataclass
class UserSession:
    id: str
    user_id: str
    name: str
    client_id: str
    secret: str = field(repr=False)
    token: str = field(repr=False)
    token_until: float
    expires: float
    team: str
    environment: Environment = "development"
    oidc_subject: str = ""
    oidc_issuer: str = ""
    oidc_session: object | None = field(default=None, repr=False)
    # MCP sessions have no active team: they see the shares received by every team of the user.
    all_teams: bool = False


class UserDirectory:
    def __init__(self, env):
        # This client stays in the trusted gateway, never in a notebook runtime.
        self.metadata = PolarisProvider(env, RustFSStorage(env))
        self.http = httpx.Client(timeout=15)
        self.url = env.get("POLARIS_URL", "http://polaris:8181").rstrip("/")
        self.s3_endpoint = env.get("USER_S3_ENDPOINT", "http://rustfs:9000")

    def close(self):
        self.metadata.close()
        self.http.close()

    def authenticate(self, client_id, secret):
        try:
            response = self.http.post(
                f"{self.url}/api/catalog/v1/oauth/tokens",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": "PRINCIPAL_ROLE:ALL",
                },
                headers={"Polaris-Realm": "POLARIS"},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The data provider is unavailable.") from exc
        if response.status_code in (400, 401, 403):
            raise ServiceError(401, "Incorrect username or client secret.")
        if response.is_error:
            raise ServiceError(502, "Sign-in to the data provider failed.")
        result = response.json()
        return result["access_token"], time.monotonic() + max(0, result["expires_in"] - 30)

    def login(self, username, secret, session_id):
        user = next((u for u in self.metadata.list_users() if u["name"] == username), None)
        if not user or not user.get("clientId"):
            raise ServiceError(401, "Incorrect username or client secret.")
        token, token_until = self.authenticate(user["clientId"], secret)
        teams = [t for t in self.metadata.list_teams() if t["id"] in user["teams"]]
        if not teams:
            raise ServiceError(403, "You are not assigned to an available team.")
        return UserSession(
            session_id,
            user["id"],
            user["name"],
            user["clientId"],
            secret,
            token,
            token_until,
            time.monotonic() + 28800,
            teams[0]["id"],
        )

    def profile(self, session):
        # Re-read metadata so deletion, team removal and moves affect active sessions.
        try:
            principal = self.metadata.require_user(f"/principals/{enc(session.user_id)}")
        except ServiceError as exc:
            if exc.status == 404:
                raise ServiceError(401, "Your user no longer exists. Sign in again.") from exc
            raise
        if session.oidc_subject and (
            principal["properties"].get("portal.oidc-subject") != session.oidc_subject
            or principal["properties"].get("portal.oidc-issuer") != session.oidc_issuer
            or principal["properties"].get("portal.identity-status", "linked") != "linked"
        ):
            raise ServiceError(403, "Your identity is no longer linked to this user.")
        user = self.metadata.user(principal)
        roles = {m["team"]: m["role"] for m in user["memberships"]}
        all_teams = self.metadata.list_teams()
        team_names = {t["id"]: t["name"] for t in all_teams}
        teams = [{**t, "role": roles[t["id"]]} for t in all_teams if t["id"] in roles]
        if not teams:
            raise ServiceError(403, "You no longer have any available teams.")
        all_databases = self.metadata.list_databases()
        databases = [
            d
            for d in all_databases
            if d["team"] in {t["id"] for t in teams}
        ]
        recipients = [t["id"] for t in teams] if session.all_teams else [session.team] if session.team in roles else []
        received, recipient = {}, {}
        if recipients:
            for share in self.metadata.list_shares(drift=True):
                if share.get("recipientTeam") not in recipients:
                    continue
                # Team-share grants sit on each member's own role, so shares received by
                # several of the user's teams combine.
                received.setdefault(share["database"], []).extend(
                    o for o in share["objects"] if o.get("granted")
                )
                recipient[share["database"]] = min(
                    recipient.get(share["database"], share["recipientTeam"]), share["recipientTeam"],
                    key=recipients.index,
                )
        owners = set(recipients) if session.all_teams else {session.team}
        shared_databases = [
            {**d, "shared": True, "sharedWithTeam": recipient[d["id"]],
             "ownerTeamName": team_names.get(d["team"], d["team"]),
             "sharedObjects": list({(o["kind"], tuple(o["namespace"]), o["name"]): o
                                    for o in received[d["id"]]}.values())}
            for d in all_databases
            if d["id"] in received and d["team"] not in owners and d["status"] == "ready"
        ]
        return {
            "sharedDatabases": shared_databases,
            "user": {"id": user["id"], "name": user["name"]},
            "teams": teams,
            "databases": [d for d in databases if d["status"] == "ready"],
            "deletingDatabases": [d for d in databases if d["status"] == "deleting"],
        }

    def manage_team(self, session):
        profile = self.profile(session)
        team = next((t for t in profile["teams"] if t["id"] == session.team), None)
        if not team or team["role"] not in ("admin", "bucket-admin"):
            raise ServiceError(403, "Only team administrators can manage databases.")
        return team

    def manage_database(self, session, id, *, allow_deleting=False):
        team = self.manage_team(session)
        catalog = self.metadata.require(f"/catalogs/{enc(id)}")
        if not self.metadata.managed(catalog):
            raise ServiceError(404, "Database not found.")
        database = self.metadata.database(catalog)
        if database["team"] != team["id"] or database["environment"] != session.environment:
            raise ServiceError(403, "This database is unavailable within your active team and environment.")
        if database["status"] == "deleting" and not allow_deleting:
            raise ServiceError(409, "This database is being deleted.")
        return database

    def create_database(self, session, name, description=""):
        self.manage_team(session)
        return self.metadata.create_database(DatabaseInput(
            name=name, description=description, team=session.team, environment=session.environment,
        ))

    def rename_database(self, session, id, name):
        self.manage_database(session, id)
        return self.metadata.rename_database(
            id, name, expected_team=session.team, expected_environment=session.environment,
        )

    def delete_database(self, session, id, confirm_name):
        database = self.manage_database(session, id, allow_deleting=True)
        if confirm_name != database["name"]:
            raise ServiceError(422, "Type the database name exactly to confirm deletion.")
        self.metadata.delete_database(
            id, expected_team=session.team, expected_environment=session.environment, expected_name=confirm_name,
        )
        return {"deleted": True, "database": id, "name": database["name"], "environment": database["environment"]}

    def team_members(self, session):
        profile = self.profile(session)
        team = next((t for t in profile["teams"] if t["id"] == session.team), None)
        if not team:
            raise ServiceError(403, "You are not a member of this team.")
        members = [
            {"id": user["id"], "name": user["name"], "role": membership["role"]}
            for user in self.metadata.list_users()
            for membership in user["memberships"]
            if membership["team"] == team["id"]
        ]
        return {"team": team["id"], "members": sorted(members, key=lambda member: member["name"].casefold())}

    def database(self, session, database, profile=None):
        profile = profile or self.profile(session)
        result = next(
            (
                d
                for d in [*profile["databases"], *profile.get("sharedDatabases", [])]
                if d["id"] == database
                and (d["team"] == session.team or d.get("sharedWithTeam") == session.team)
                and d["environment"] == session.environment
            ),
            None,
        )
        if not result:
            raise ServiceError(403, "This database is unavailable within your active team and environment.")
        return result

    def share_database(self, session, database):
        """The database and caller, only for a current administrator of the owning team.

        Shares are written with the platform identity, so this check is the whole
        authorization. It is re-read from Polaris on every request, never cached.
        """
        profile = self.profile(session)
        result = self.database(session, database, profile)
        if result.get("shared"):
            raise ServiceError(403, "Only the owning team can manage this data share.")
        role = next(t["role"] for t in profile["teams"] if t["id"] == result["team"])
        if role not in ("admin", "bucket-admin"):
            raise ServiceError(403, "Only team administrators can manage data shares.")
        return result, profile["user"]

    def share(self, session, id):
        principal = self.metadata.require_share(id)
        self.share_database(session, principal["properties"]["portal.database"])
        return principal

    def request(self, session, path):
        if session.token_until <= time.monotonic():
            if session.oidc_subject:
                raise ServiceError(401, "Your Keycloak session expired. Sign in again.")
            session.token, session.token_until = self.authenticate(session.client_id, session.secret)
        try:
            response = self.http.get(
                self.url + path,
                headers={
                    "Authorization": f"Bearer {session.token}",
                    "Polaris-Realm": "POLARIS",
                },
            )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The data provider is unavailable.") from exc
        if response.is_error:
            status = response.status_code if response.status_code in (401, 403, 404) else 502
            raise ServiceError(status, "This content is unavailable with your permissions.")
        return response.json()

    def login_oidc(self, claims, token, lifetime, session_id):
        mapping = claims.get("polaris", {})
        name = mapping.get("principal_name")
        if not isinstance(name, str) or not name.startswith("portal-"):
            raise ServiceError(403, "Your identity is not linked to a platform user.")
        principal = self.metadata.require_user(f"/principals/{enc(name)}")
        # Polaris's management API does not expose numeric entity IDs. Zero
        # requests lookup by the immutable portal principal name, not username.
        if mapping.get("principal_id") != 0:
            raise ServiceError(403, "Your identity mapping is invalid.")
        user = self.metadata.user(principal)
        until = time.monotonic() + lifetime
        session = UserSession(
            session_id, user["id"], user["name"], "", "", token, until, until, "",
            oidc_subject=claims["sub"], oidc_issuer=claims["iss"],
        )
        profile = self.profile(session)
        session.team = profile["teams"][0]["id"]
        # Exercise the external token against Polaris before accepting the login.
        if profile["databases"]:
            self.request(session, "/api/catalog/v1/config?warehouse=" + enc(profile["databases"][0]["id"]))
        return session

    def pages(self, session, path, key):
        values, seen, token = [], set(), None
        while True:
            url = path + (("&" if "?" in path else "?") + f"pageToken={enc(token)}" if token else "")
            page = self.request(session, url)
            values.extend(page.get(key, []))
            token = page.get("next-page-token")
            if not token:
                return values
            if token in seen:
                raise ServiceError(502, "The data provider returned invalid pagination.")
            seen.add(token)

    def contents(self, session, database, namespace):
        info = self.database(session, database)
        if info.get("shared"):
            objects = info["sharedObjects"]
            children = sorted({tuple(o["namespace"][:len(namespace) + 1]) for o in objects
                               if o["namespace"][:len(namespace)] == namespace
                               and len(o["namespace"]) > len(namespace)})
            return {
                "database": database, "namespace": namespace,
                "namespaces": [list(parts) for parts in children],
                **{kind + "s": sorted(
                    [{"name": o["name"], "namespace": o["namespace"]} for o in objects
                     if o["kind"] == kind and o["namespace"] == namespace], key=lambda o: o["name"]
                ) for kind in ("table", "view")},
            }
        prefix = f"/api/catalog/v1/{enc(database)}"
        encoded = enc("\x1f".join(namespace))
        children = self.pages(
            session, f"{prefix}/namespaces" + (f"?parent={encoded}" if namespace else ""), "namespaces"
        )
        result = {
            "database": database,
            "namespace": namespace,
            "namespaces": sorted(children),
            "tables": [],
            "views": [],
        }
        if namespace:
            for kind in ("tables", "views"):
                items = self.pages(session, f"{prefix}/namespaces/{encoded}/{kind}", "identifiers")
                result[kind] = sorted(
                    [{"name": t["name"], "namespace": t["namespace"]} for t in items], key=lambda t: t["name"]
                )
        return result

    def details(self, session, database, namespace, kind, name=None):
        database_info = self.database(session, database)
        if kind == "database":
            return {
                "kind": kind,
                "database": database_info,
                "catalogUri": self.metadata.public_url + "/api/catalog",
            }
        if database_info.get("shared"):
            objects = database_info["sharedObjects"]
            if kind == "namespace":
                if not any(o["namespace"][:len(namespace)] == namespace for o in objects):
                    raise ServiceError(404, "Namespace not shared with this team.")
                return {"kind": kind, "namespace": namespace, "properties": {}}
            if not any(o["kind"] == kind and o["namespace"] == namespace and o["name"] == name
                       for o in objects):
                raise ServiceError(403, "This object is not shared with your active team.")
        path = f"/api/catalog/v1/{enc(database)}/namespaces/{enc(chr(31).join(namespace))}"
        if kind == "namespace":
            loaded = self.request(session, path)
            return {
                "kind": kind,
                "namespace": namespace,
                "properties": properties(loaded.get("properties", {})),
            }
        loaded = self.request(session, f"{path}/{kind}s/{enc(name)}")
        return {"name": name, "namespace": namespace, **object_details(kind, loaded)}

    def preview_request(self, session, database, namespace, name, snapshot_id, limit):
        details = self.details(session, database, namespace, "table", name)
        selected = snapshot_id or details["currentSnapshotId"]
        if selected is not None and selected not in {s["id"] for s in details["snapshots"]}:
            raise ServiceError(404, "This snapshot is no longer available. Refresh the table.")
        return {
            "uri": self.url + "/api/catalog",
            "database": database,
            "namespace": namespace,
            "table": name,
            "snapshotId": selected,
            "limit": limit,
            "token": session.token,
            "s3Endpoint": self.s3_endpoint,
        }
