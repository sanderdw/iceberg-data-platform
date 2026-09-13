"""Trusted metadata directory plus catalog requests made as the actual user."""

import time
from dataclasses import dataclass, field

import httpx

from server.models import Environment, ServiceError
from server.polaris import PolarisProvider, enc


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


class UserDirectory:
    def __init__(self, env):
        # This client stays in the trusted gateway, never in a notebook runtime.
        self.metadata = PolarisProvider(env, None)
        self.http = httpx.Client(timeout=15)
        self.url = env.get("POLARIS_URL", "http://polaris:8181").rstrip("/")

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
