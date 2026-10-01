"""Fakes for the platform side: the Bridge (contract 0.1), Keycloak tokens, Polaris and the query worker.

Team A owns `sales` and publishes the flights model in it; the partner team shares its own flights
model with team A. The worker fake runs the compiled SQL on the local flights data.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from conversationalbi.app import create_app
from conversationalbi.auth import Caller
from conversationalbi.config import Settings
from conversationalbi.errors import CbiError
from conversationalbi.semantic import worker
from tests import flights

TEAM = "team-" + "a" * 32
PARTNER = "team-" + "b" * 32
OTHER = "team-" + "c" * 32
OWN_DB = "db-" + "1" * 32
PROD_DB = "db-" + "2" * 32
SHARED_DB = "db-" + "3" * 32
ISSUER = "http://localhost:8080/realms/iceberg"
FLIGHTS = {"kind": "semantic-model", "namespace": ["ai_flights"], "name": "flights"}
TABLES = [{"kind": "table", "namespace": ["ai_flights"], "name": t}
          for t in ("flights", "routes", "airports", "carriers", "aircraft", "runways")]


def settings(**overrides):
    values = {"bridge_url": "http://bridge:3005", "extension_id": "conversationalbi",
              "client_id": "ext-conversationalbi", "client_secret": "s" * 48,
              "mcp_client_id": "ext-conversationalbi-mcp", "issuer": ISSUER, "internal_issuer": ISSUER,
              "origin": "http://localhost:3007", "runtime_key": "k" * 32, "model": "test:flights",
              "allow_test_model": True}
    return Settings(**{**values, **overrides})


class FakeBridge:
    def __init__(self):
        self.roles = {"alice": {TEAM: "admin"}, "bob": {TEAM: "reader"}, "dave": {OTHER: "admin"},
                      "erin": {PARTNER: "admin"}}
        self.capabilities_ = ["user-context", "automation-principals", "automation-tokens", "shared-data"]
        self.automation_ = []
        self.shared_objects = [FLIGHTS, *TABLES]
        self.tokens = []
        self.me_calls = []

    async def discovery(self, refresh=False):
        return {"contractVersion": "0.1.0", "capabilities": self.capabilities_,
                "catalog": {"internalUri": "http://polaris-control-plane:8181/api/catalog"},
                "storage": {"endpoint": "http://localhost:9000", "internalEndpoint": "http://rustfs:9000"},
                "userPortalUrl": "http://localhost:3002"}

    async def capabilities(self):
        return set(self.capabilities_)

    async def close(self):
        pass

    async def me(self, token, *, fresh=False):
        self.me_calls.append((token, fresh))
        roles = self.roles[token]
        databases = [{"id": OWN_DB, "name": "sales", "team": TEAM, "environment": "development", "status": "ready"},
                     {"id": PROD_DB, "name": "sales", "team": TEAM, "environment": "production", "status": "ready"}]
        return {
            "user": {"id": "portal-" + token.ljust(32, "0")[:32], "name": token},
            "memberships": [{"team": t, "teamName": t[:9], "role": r} for t, r in roles.items()],
            "databases": [d for d in databases if d["team"] in roles],
            "sharedDatabases": [
                {"id": SHARED_DB, "name": "flights", "team": PARTNER, "environment": "development", "status": "ready",
                 "sharedWithTeam": TEAM, "ownerTeamName": "partner-team", "recipientTeams": [TEAM],
                 "sharedObjects": self.shared_objects}
            ] if TEAM in roles else [],
        }

    async def team_automation(self, token, team):
        return [a for a in self.automation_ if a["team"] == team]

    async def enable(self, token, team, environment):
        if self.roles[token].get(team) not in ("admin", "bucket-admin"):
            raise CbiError(403, "Only team administrators can enable an extension.", "forbidden_role")
        item = {"id": "svc-" + str(len(self.automation_)).rjust(32, "0"), "team": team, "environment": environment,
                "extension": "conversationalbi", "status": "active", "createdBy": token}
        self.automation_.append(item)
        return item

    async def revoke(self, token, id):
        self.automation_ = [a for a in self.automation_ if a["id"] != id]
        return {"revoked": True}

    async def automation(self, *, fresh=False):
        return list(self.automation_)

    async def principal_for(self, team, environment):
        for item in self.automation_:
            if item["team"] == team and item["environment"] == environment:
                return item
        raise CbiError(409, "Conversational BI is not enabled.", "environment_not_enabled")

    async def catalog_token(self, id, access, purpose):
        self.tokens.append((id, access, purpose))
        return {"accessToken": f"token-{id}-{access}", "expiresIn": 3600}


class FakeVerifier:
    async def verify(self, token):
        if token not in ("alice", "bob", "dave", "erin"):
            raise CbiError(401, "Sign in again.", "unauthorized")
        return Caller(token, "sub-" + token, token, "ext-conversationalbi-mcp", 4102444800.0)


class FakeCatalog:
    """Polaris metadata: the flights model in team A's development database and in the partner's shared database."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.forgotten = []

    async def close(self):
        pass

    async def uri(self):
        return "http://polaris-control-plane:8181/api/catalog"

    async def token(self, principal):
        return (await self.bridge.catalog_token(principal, "read", "test"))["accessToken"]

    def forget(self, principal):
        self.forgotten.append(principal)

    async def namespaces(self, principal, database):
        return [["ai_flights"], ["staging"]] if database == OWN_DB else []

    async def model_names(self, principal, database, namespace):
        return ["flights"] if (database, namespace) == (OWN_DB, ["ai_flights"]) else []

    async def load_model(self, principal, database, namespace, name):
        if (database, namespace, name) not in ((OWN_DB, ["ai_flights"], "flights"),
                                               (SHARED_DB, ["ai_flights"], "flights")):
            raise CbiError(404, "This semantic model does not exist.", "model_not_readable")
        return flights.ENVELOPE

    async def table_schema(self, principal, database, namespace, table):
        return {c["name"]: c["type"] for c in flights.TABLE_SCHEMAS[table]}


class FakeExecutor:
    """Runs the job's SQL on the local flights data, as the worker would after binding."""

    def __init__(self):
        self.jobs = []

    async def run(self, job):
        self.jobs.append(job)
        connection = flights.local(job["bindings"])
        try:
            result = worker.execute(connection, job["sql"], job["params"], job["limit"])
        finally:
            connection.close()
        # Every table was last committed at 2026-09-01 08:00 UTC.
        return {**result, "snapshots": {b["view"]: {"id": 1, "committedAt": 1788249600000} for b in job["bindings"]}}


@pytest.fixture
def stack():
    bridge = FakeBridge()
    runtime_calls = []

    def runtime(request):
        runtime_calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'data: {"type":"RUN_STARTED"}\n\n'),
                              headers={"content-type": "text/event-stream"})

    executor = FakeExecutor()
    app = create_app(settings(), bridge=bridge, verifier=FakeVerifier(), catalog=FakeCatalog(bridge),
                     executor=executor,
                     runtime=httpx.AsyncClient(base_url="http://runtime", transport=httpx.MockTransport(runtime)))
    with TestClient(app) as client:
        def call(method, path, user="alice", **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {user}"}, **kwargs)

        yield SimpleNamespace(client=client, call=call, bridge=bridge, app=app, services=app.state.services,
                              executor=executor, runtime_calls=runtime_calls)
