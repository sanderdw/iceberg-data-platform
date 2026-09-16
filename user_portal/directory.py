"""Trusted metadata directory plus catalog requests made as the actual user."""

import time
from dataclasses import dataclass, field

import httpx

from server.models import Environment, ServiceError
from server.polaris import PolarisProvider, enc

from .catalog import object_details, properties


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


class UserDirectory:
    def __init__(self, env):
        # This client stays in the trusted gateway, never in a notebook runtime.
        self.metadata = PolarisProvider(env, None)
        self.http = httpx.Client(timeout=15)
        self.url = env.get("POLARIS_URL", "http://polaris:8181").rstrip("/")
        self.s3_endpoint = env.get("USER_S3_ENDPOINT", "http://rustfs:9000")

    def close(self):
        self.metadata.http.close()
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
            principal = self.metadata.require(f"/principals/{enc(session.user_id)}")
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
        teams = [t for t in self.metadata.list_teams() if t["id"] in user["teams"]]
        if not teams:
            raise ServiceError(403, "You no longer have any available teams.")
        databases = [
            d
            for d in self.metadata.list_databases()
            if d["team"] in {t["id"] for t in teams} and d["status"] == "ready"
        ]
        return {
            "user": {"id": user["id"], "name": user["name"], "role": user["role"]},
            "teams": teams,
            "databases": databases,
        }

    def database(self, session, database, profile=None):
        profile = profile or self.profile(session)
        result = next(
            (
                d
                for d in profile["databases"]
                if d["id"] == database
                and d["team"] == session.team
                and d["environment"] == session.environment
            ),
            None,
        )
        if not result:
            raise ServiceError(403, "This database is unavailable within your active team and environment.")
        return result

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
        principal = self.metadata.require(f"/principals/{enc(name)}")
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
        self.database(session, database)
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
