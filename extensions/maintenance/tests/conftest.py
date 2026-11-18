"""Fakes for the platform side: the Bridge, Keycloak tokens and the launcher.

The launcher fake finishes a run immediately and writes what the Go runner would: result.json with each
table's health and actions, and run.log.
"""

import asyncio
import base64
import io
import json
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from maintenance.app import create_app
from maintenance.auth import Caller
from maintenance.config import Settings
from maintenance.errors import MaintenanceError

TEAM = "team-" + "a" * 32
OTHER = "team-" + "b" * 32
ISSUER = "http://localhost:8080/realms/iceberg"
DATABASES = {
    ("grid-capacity", "development"): "db-" + "1" * 32, ("grid-capacity", "production"): "db-" + "2" * 32,
    ("grid-assets", "development"): "db-" + "3" * 32,
}
DEV = DATABASES[("grid-capacity", "development")]
TABLES = ["raw.feeder_load_readings", "marts.feeder_capacity_daily", "raw.transformer_register"]


def settings(tmp_path, **overrides):
    return Settings(bridge_url="http://bridge:3005", extension_id="maintenance", client_id="ext-maintenance",
                    client_secret="s" * 48, mcp_client_id="ext-maintenance-mcp", issuer=ISSUER, internal_issuer=ISSUER,
                    origin="http://localhost:3013", state_dir=Path(tmp_path), launcher_url="http://launcher",
                    launcher_token="l" * 40, **overrides)


def health(name, small=0, files=10, snapshots=4, expirable=0, deletes=0, manifests=3):
    now = int(time.time() * 1000)
    return {
        "formatVersion": 2, "snapshots": snapshots, "oldestSnapshotMs": now - 6 * 86400000, "currentSnapshotMs": now,
        "currentSnapshotId": 1, "branches": 1, "tags": [], "snapshotDays": [0] * 13 + [snapshots],
        "expirableSnapshots": expirable, "dataFiles": files, "dataBytes": files * 50 * 1048576, "records": files * 1000,
        "smallFiles": small, "smallFileThreshold": 96 * 1048576, "sizeHistogram": [small, 0, 0, 0, files - small, 0, 0],
        "compactableFiles": small, "compactableBytes": small * 1048576, "estimatedFilesAfter": 1 if small else 0,
        "positionDeleteFiles": deletes, "equalityDeleteFiles": 0, "deletionVectors": 0, "manifests": manifests,
        "gcEnabled": True, "sortOrder": False, "partitionFields": 1,
        "producer": "dlt" if name.startswith("raw.") else "dbt", "expiryOverrides": {},
        "location": f"s3://bucket/{name}",
    }


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
            "memberships": [{"team": t, "teamName": "grid-planning" if t == TEAM else "other", "role": r}
                            for t, r in roles.items()],
            "databases": [{"id": id, "name": name, "team": TEAM, "environment": env, "status": "ready"}
                          for (name, env), id in DATABASES.items() if TEAM in roles],
            "sharedDatabases": [],
        }

    async def team_automation(self, token, team):
        return [a for a in self.automation if a["team"] == team]

    async def enable(self, token, team, environment):
        if self.roles[token].get(team) not in ("admin", "bucket-admin"):
            raise MaintenanceError(403, "Only team administrators can enable an extension.", "forbidden_role")
        item = {"id": "svc-" + str(len(self.automation)).rjust(32, "0"), "team": team, "environment": environment,
                "extension": "maintenance", "status": "active", "createdBy": token}
        self.automation.append(item)
        return item

    async def revoke(self, token, id):
        self.automation = [a for a in self.automation if a["id"] != id]
        return {"revoked": True}

    async def principal_for(self, team, environment):
        for item in self.automation:
            if item["team"] == team and item["environment"] == environment:
                return item
        raise MaintenanceError(409, "Maintenance is not enabled.", "environment_not_enabled")

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
            raise MaintenanceError(401, "Sign in again.", "unauthorized")
        return Caller(token, "sub-" + token, token, "ext-maintenance-mcp", 4102444800.0)


class FakeLauncher:
    """Answers the launcher protocol and writes a finished run's artifacts, as the Go runner would."""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.started = {}
        self.exit_code = 0
        self.tables = list(TABLES)
        self.fail_table = None
        self.hold = False
        self.killed = []

    def result(self, job):
        maintain, dry = job["kind"] == "maintain", job["dryRun"]
        tables = []
        for name in self.tables:
            if job["tables"] and name not in job["tables"]:
                continue
            small = 30 if name == TABLES[0] else 0
            entry = {"name": name, "status": "success", "actions": [], "health": health(name, small=small, files=40,
                                                                                          expirable=2)}
            if maintain and name in job["exclude"]:
                entry["status"] = "skipped"
                entry["actions"] = [{"operation": "excluded", "status": "skipped", "detail": "Excluded."}]
            elif maintain:
                status = "planned" if dry else "done"
                entry["actions"] = [
                    {"operation": "compaction", "status": status if small else "skipped",
                     "detail": f"Rewrote {small} files into 1." if small else "No files need compaction.",
                     "metrics": {"inputFiles": small}},
                    {"operation": "expireSnapshots", "status": status, "detail": "Expired 2 snapshots.",
                     "metrics": {"snapshots": 2}}]
                if not dry:
                    entry["health"] = health(name, small=0, files=40 - small + (1 if small else 0), expirable=0)
            if name == self.fail_table:
                entry = {"name": name, "status": "failed", "error": "commit conflict", "actions": []}
            tables.append(entry)
        totals = {"tables": len(tables), "failed": sum(t["status"] == "failed" for t in tables),
                  "snapshotsExpired": 0 if dry or not maintain else 2 * len(tables), "filesCompacted": 0,
                  "filesWritten": 0, "bytesCompacted": 0, "deleteFilesRemoved": 0, "orphanFiles": 0, "orphanBytes": 0,
                  "manifestsRewritten": 0}
        status = "partial" if totals["failed"] else "success"
        return {"status": status, "kind": job["kind"], "dryRun": dry, "runnerVersion": "0.0.1",
                "icebergGo": "v0.7.1-0.20261118175253-e7a4d96b505d", "tables": tables, "totals": totals}

    def handler(self, request):
        if request.method == "POST" and request.url.path == "/runs":
            body = json.loads(request.content)
            staged = tarfile.open(fileobj=io.BytesIO(base64.b64decode(body["archive"])))
            job = json.load(staged.extractfile("job.json"))
            self.started[body["id"]] = {"token": body["token"], "job": job, "files": staged.getnames(),
                                        "timeout": body["timeout"]}
            out = self.artifacts / body["id"] / "out"
            out.mkdir(parents=True)
            (out / "run.log").write_text(f"Maintenance runner 0.0.1: {job['kind']}\n")
            (out / "result.json").write_text(json.dumps(self.result(job)))
            return httpx.Response(202, json={"id": body["id"], "state": "running"})
        if request.method == "GET" and request.url.path.endswith("/logs"):
            return httpx.Response(200, text="log\n")
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

        def run(mode="inspect", user="bob", database=DEV, **fields):
            return call("POST", f"/databases/{database}/runs", user, json={"mode": mode, "wait": 5, **fields})

        yield SimpleNamespace(client=client, call=call, run=run, bridge=bridge, launcher=launcher, app=app,
                              services=app.state.services, settings=config)


def _fast_sleep(original):
    async def sleep(delay, *args, **kwargs):
        return await original(min(delay, 0.01), *args, **kwargs)
    return sleep
