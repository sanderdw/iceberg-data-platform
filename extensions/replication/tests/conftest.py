"""Fakes for the platform side: the Bridge, Keycloak tokens, DNS and the launcher.

The launcher fake finishes a run immediately and writes what a real runner would: result.json,
run.log and OLake's state in out/state.
"""

import asyncio
import base64
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from replication.app import create_app
from replication.auth import Caller
from replication.config import Settings
from replication.errors import ReplicationError

TEAM = "team-" + "a" * 32
OTHER = "team-" + "b" * 32
ISSUER = "http://localhost:8080/realms/iceberg"
DATABASES = {
    ("sales", "development"): "db-" + "1" * 32, ("sales", "production"): "db-" + "2" * 32,
    ("raw-data", "development"): "db-" + "3" * 32,
}
# Host names the fake DNS knows; everything else resolves to a public address.
PRIVATE_HOSTS = {"intranet.example.com": "10.0.0.5", "metadata.example.com": "169.254.169.254",
                 "loop.example.com": "127.0.0.1"}
SOURCE = {"host": "db.example.com", "port": 5432, "database": "crm", "username": "olake_reader",
          "password": "$secret:password", "tls": "verify-full", "slot": "iceberg_crm", "publication": "iceberg_crm"}
TABLES = {"public.orders": {"mode": "cdc", "partition": "/{created_at,month}"}, "public.customers": {"mode": "cdc"}}


class ToolRefused(Exception):
    """An MCP tool answered with an error."""


def settings(tmp_path, **overrides):
    return Settings(bridge_url="http://bridge:3005", extension_id="replication", client_id="ext-replication",
                    client_secret="s" * 48, mcp_client_id="ext-replication-mcp", issuer=ISSUER, internal_issuer=ISSUER,
                    origin="http://localhost:3014", state_dir=Path(tmp_path), launcher_url="http://launcher",
                    launcher_token="l" * 40, secret_key="ab" * 32, **overrides)


class FakeBridge:
    def __init__(self):
        self.roles = {"alice": {TEAM: "admin"}, "bob": {TEAM: "writer"}, "carol": {TEAM: "reader"},
                      "dave": {OTHER: "admin"}}
        self.automation = []
        self.tokens = []
        self.token_lifetime = 3600

    async def discovery(self, refresh=False):
        return {"contractVersion": "0.1.0",
                "catalog": {"internalUri": "http://polaris-control-plane:8181/api/catalog"},
                "storage": {"endpoint": "http://localhost:9000", "internalEndpoint": "http://rustfs:9000"},
                "userPortalUrl": "http://localhost:3002"}

    async def close(self):
        pass

    async def me(self, token):
        roles = self.roles[token]
        return {
            "user": {"id": "portal-" + token.ljust(32, "0")[:32], "name": token},
            "memberships": [{"team": t, "teamName": t[:9], "role": r} for t, r in roles.items()],
            "databases": [{"id": id, "name": name, "team": TEAM, "environment": env, "status": "ready"}
                          for (name, env), id in DATABASES.items() if TEAM in roles],
            "sharedDatabases": [],
        }

    async def team_automation(self, token, team):
        return [a for a in self.automation if a["team"] == team]

    async def enable(self, token, team, environment):
        if self.roles[token].get(team) not in ("admin", "bucket-admin"):
            raise ReplicationError(403, "Only team administrators can enable an extension.", "forbidden_role")
        item = {"id": "svc-" + str(len(self.automation)).rjust(32, "0"), "team": team, "environment": environment,
                "extension": "replication", "status": "active", "createdBy": token}
        self.automation.append(item)
        return item

    async def revoke(self, token, id):
        self.automation = [a for a in self.automation if a["id"] != id]
        return {"revoked": True}

    async def principal_for(self, team, environment):
        for item in self.automation:
            if item["team"] == team and item["environment"] == environment:
                return item
        raise ReplicationError(409, "Replication is not enabled.", "environment_not_enabled")

    async def scope(self, id):
        item = next(a for a in self.automation if a["id"] == id)
        return {**item, "databases": [
            {"id": db, "name": name, "environment": env, "team": TEAM, "status": "ready", "storageLocation": "s3://x/"}
            for (name, env), db in DATABASES.items() if env == item["environment"]]}

    async def catalog_token(self, id, access, purpose):
        self.tokens.append((id, access, purpose))
        return {"accessToken": f"token-{access}", "expiresIn": self.token_lifetime}


class FakeVerifier:
    async def verify(self, token):
        if token not in ("alice", "bob", "carol", "dave"):
            raise ReplicationError(401, "Sign in again.", "unauthorized")
        return Caller(token, "sub-" + token, token, "ext-replication-mcp", 4102444800.0)


class FakeLauncher:
    """Answers the launcher protocol and writes a finished run's artifacts."""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.started = {}
        self.hosts = {}
        self.exit_code = 0
        self.rows = 3
        self.status = "success"
        self.log = "Total records read: 3\n"
        # hold: runs stay "running" until they are stopped; killed: ids the gateway stopped.
        self.hold = False
        self.killed = []
        self.on_start = None

    def handler(self, request):
        if request.method == "POST" and request.url.path == "/runs":
            body = json.loads(request.content)
            staged = tarfile.open(fileobj=io.BytesIO(base64.b64decode(body["archive"])))
            job = json.load(staged.extractfile("job.json"))
            state = None
            if "state/state.json" in staged.getnames():
                state = json.load(staged.extractfile("state/state.json"))
            self.hosts[body["id"]] = body.get("hosts")
            self.started[body["id"]] = {"token": body["token"], "secrets": body["secrets"], "job": job,
                                        "files": staged.getnames(), "timeout": body["timeout"], "state": state}
            out = self.artifacts / body["id"] / "out"
            out.mkdir(parents=True)
            (out / "run.log").write_text(self.log)
            if job["kind"] == "check":
                result = {"kind": "check", "status": "success", "connected": True, "ready": False,
                          "message": "required CDC prerequisites not met: publication",
                          "checks": [{"name": "wal_level", "required": True, "passed": True,
                                      "currentValue": "logical", "recommendedValue": "logical", "description": "x"},
                                     {"name": "publication", "required": True, "passed": False,
                                      "currentValue": "not found", "recommendedValue": "exists", "description": "y"}],
                          "streams": [{"name": "public.orders", "primaryKey": ["id"], "cursorFields": ["updated_at"],
                                       "modes": ["cdc", "incremental"], "columns": 9}]}
            else:
                (out / "state").mkdir()
                runs = (state or {}).get("runs", 0) + 1
                (out / "state" / "state.json").write_text(json.dumps({"runs": runs, "global": {"state": {
                    "lsn": f"0/{runs}"}}}))
                tables = [{"name": name, "table": spec["table"], "records": self.rows, "replaced": 1, "deleteFiles": 1}
                          for name, spec in job["tables"].items()]
                result = {"kind": "sync", "status": self.status if self.exit_code == 0 else "failed",
                          "olakeVersion": "v1.2.0", "tables": tables, "readUpTo": f"0/{runs}", "recordsRead": 3,
                          "firstCopyComplete": self.status == "success",
                          "published": {"tables": [t["table"] for t in tables], "errors": []},
                          "error": "" if self.exit_code == 0 else "password authentication failed"}
            (out / "result.json").write_text(json.dumps(result))
            if self.on_start:
                self.on_start(body["id"])
            return httpx.Response(202, json={"id": body["id"], "state": "running"})
        if request.method == "GET" and request.url.path.endswith("/logs"):
            return httpx.Response(200, text=self.log)
        if request.method == "GET" and request.url.path.startswith("/runs/"):
            id = request.url.path.split("/")[2]
            if self.hold and id not in self.killed:
                return httpx.Response(200, json={"state": "running"})
            code = 137 if id in self.killed else self.exit_code
            return httpx.Response(200, json={"state": "finished", "exitCode": code, "timedOut": False})
        if request.method == "DELETE" and not request.url.path.endswith("/artifacts"):
            self.killed.append(request.url.path.split("/")[2])
            return httpx.Response(200, json={"cancelled": True})
        if request.method == "DELETE":
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"error": "unknown"})


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    monkeypatch.setattr("replication.validate.resolve_addresses",
                        lambda host: [PRIVATE_HOSTS.get(host, "93.184.216.34")])


def make_stack(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    config = settings(tmp_path, **overrides)
    bridge, verifier = FakeBridge(), FakeVerifier()
    launcher = FakeLauncher(tmp_path / "artifacts")
    app = create_app(config, bridge=bridge, verifier=verifier)
    runs = app.state.services.runs
    runs.http = httpx.AsyncClient(base_url="http://launcher", transport=httpx.MockTransport(launcher.handler))
    return config, bridge, launcher, app


@pytest.fixture
def stack(tmp_path, monkeypatch):
    config, bridge, launcher, app = make_stack(tmp_path, monkeypatch)
    with TestClient(app) as client:
        def call(method, path, user="alice", **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {user}"}, **kwargs)

        def create(user="alice", **fields):
            body = {"team": TEAM, "name": "crm", "database": "sales", "namespace": "raw_crm",
                    "sourceType": "postgres", "source": SOURCE, "tables": TABLES, **fields}
            return call("POST", "/replications", user, json=body)

        def rpc(method, user, **params):
            response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                   headers={"Authorization": f"Bearer {user}",
                                            "Accept": "application/json, text/event-stream"})
            return response.json()["result"]

        def tool(name, user="alice", **arguments):
            """Calls an MCP tool as an agent does. Returns its result; a refusal raises ToolRefused with its text."""
            result = rpc("tools/call", user, name=name, arguments=arguments)
            text = "".join(block.get("text", "") for block in result["content"])
            if result.get("isError"):
                raise ToolRefused(text)
            return json.loads(text)

        def tools():
            return {t["name"]: t for t in rpc("tools/list", "alice")["tools"]}

        yield SimpleNamespace(client=client, call=call, create=create, tool=tool, tools=tools, bridge=bridge,
                              launcher=launcher, app=app, services=app.state.services, settings=config)


def _fast_sleep(original):
    async def sleep(delay, *args, **kwargs):
        # Polling loops finish at once; the scheduler's long sleep still yields.
        return await original(min(delay, 0.01), *args, **kwargs)
    return sleep
