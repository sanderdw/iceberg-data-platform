"""Fakes for the platform side: the Bridge, Keycloak tokens, Apache Polaris and the launcher.

The Polaris fake answers the Iceberg REST listing and Polaris's Policy API as Polaris 1.9 does: content
validated against the system types, versions, one inheritable policy per type and target, and applicable
policies resolved with table over namespace over catalog. The launcher fake finishes a run immediately and
writes what the Go runner would: result.json with each table's health and actions, and run.log.
"""

import asyncio
import base64
import io
import json
import re
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

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
TABLES = ["marts.feeder_capacity_daily", "raw.feeder_load_readings", "raw.transformer_register"]
READINGS = "raw.feeder_load_readings"
CATALOG_URI = "http://polaris-control-plane:8181/api/catalog"
SYSTEM_TYPES = {"system.data-compaction", "system.metadata-compaction", "system.snapshot-expiry",
                "system.orphan-file-removal"}


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
        return {"contractVersion": "1.0.0",
                "catalog": {"internalUri": CATALOG_URI},
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
        return {"deleted": True, "automationPrincipal": id}

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


class PolarisError(Exception):
    def __init__(self, status, kind, message):
        super().__init__(message)
        self.status, self.kind = status, kind


def check_content(ptype, content):
    """What Polaris's validator checks for the system types (JSON schemas of 2025-02-03)."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        raise PolarisError(400, "InvalidPolicyException", "Invalid policy content") from None
    allowed = {"version", "enable", "config"} | (
        {"max_orphan_file_age_in_days", "locations"} if ptype == "system.orphan-file-removal" else set())
    if not isinstance(parsed, dict) or set(parsed) - allowed or not isinstance(parsed.get("enable"), bool):
        raise PolarisError(400, "InvalidPolicyException", "Invalid policy content")
    if parsed.get("version", "2025-02-03") != "2025-02-03":
        raise PolarisError(400, "InvalidPolicyException", "Invalid policy version")
    config = parsed.get("config", {})
    if not isinstance(config, dict) or any(not isinstance(v, (str, int, float, bool)) for v in config.values()):
        raise PolarisError(400, "InvalidPolicyException", "Invalid policy config")


class FakePolaris:
    """Apache Polaris 1.9 as Maintenance uses it: namespaces and tables, and the Policy API."""

    def __init__(self):
        self.catalogs = {}
        for db in DATABASES.values():
            self.catalogs[db] = {"namespaces": {}, "tables": {}, "policies": {}, "mappings": {}}
            for name in TABLES:
                self.add_table(db, name)
        self.requests = []

    def add_table(self, db, name):
        *namespace, table = name.split(".")
        catalog = self.catalogs[db]
        for i in range(1, len(namespace) + 1):
            catalog["namespaces"].setdefault(tuple(namespace[:i]), {})
        catalog["tables"].setdefault(tuple(namespace), set()).add(table)

    def drop_table(self, db, name):
        *namespace, table = name.split(".")
        catalog = self.catalogs[db]
        catalog["tables"][tuple(namespace)].discard(table)
        # Polaris drops a table's attachments with it.
        for targets in catalog["mappings"].values():
            targets.discard(("table-like", (*namespace, table)))

    def attached(self, db, target):
        """The policies attached to one target: {(namespace, name): policy}."""
        catalog = self.catalogs[db]
        return {key: catalog["policies"][key] for key, targets in catalog["mappings"].items() if target in targets}

    def applicable(self, db, namespace=None, table=None):
        catalog = self.catalogs[db]
        chain = [("catalog", ())]
        if namespace:
            if tuple(namespace) not in catalog["namespaces"]:
                raise PolarisError(404, "NoSuchNamespaceException", "Namespace does not exist")
            chain += [("namespace", tuple(namespace[:i])) for i in range(1, len(namespace) + 1)]
        if table:
            if table not in catalog["tables"].get(tuple(namespace), set()):
                raise PolarisError(404, "NoSuchTableException", "Table does not exist")
            chain.append(("table-like", (*namespace, table)))
        found = {}
        # The most specific level wins per type: table over namespace over catalog.
        for depth, target in enumerate(chain):
            for (ns, name), policy in self.attached(db, target).items():
                found[policy["type"]] = {"policy-type": policy["type"], "inheritable": True, "name": name,
                                         "description": policy["description"], "content": policy["content"],
                                         "version": policy["version"], "inherited": depth < len(chain) - 1,
                                         "namespace": list(ns)}
        return list(found.values())

    def target(self, db, target):
        kind, path = target.get("type"), tuple(target.get("path") or ())
        catalog = self.catalogs[db]
        if kind == "catalog":
            return ("catalog", ())
        if kind == "namespace" and path in catalog["namespaces"]:
            return ("namespace", path)
        if kind == "table-like" and len(path) > 1 and path[-1] in catalog["tables"].get(path[:-1], set()):
            return ("table-like", path)
        raise PolarisError(404, "NoSuchTargetException", "The given target does not exist")

    def handler(self, request):
        self.requests.append((request.method, request.url.path, request.headers.get("authorization")))
        try:
            return self.handle(request)
        except PolarisError as exc:
            return httpx.Response(exc.status, json={"error": {"message": str(exc), "type": exc.kind,
                                                              "code": exc.status}})

    def handle(self, request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in ("token-read", "token-write") or request.headers.get("polaris-realm") != "POLARIS":
            raise PolarisError(401, "NotAuthorizedException", "Not authorized")
        write = request.method != "GET"
        # The catalog role reader only reads; writer holds CATALOG_MANAGE_CONTENT, which includes the policies.
        if write and token != "token-write":
            raise PolarisError(403, "ForbiddenException", "Principal is not authorized")
        path = request.url.path.removeprefix("/api/catalog")
        match = re.match(r"^/(v1|polaris/v1)/([^/]+)(/.*)?$", path)
        if not match or match.group(2) not in self.catalogs:
            raise PolarisError(404, "NotFoundException", "Unknown catalog")
        api, db, rest = match.group(1), match.group(2), match.group(3) or ""
        catalog = self.catalogs[db]
        body = json.loads(request.content) if request.content else {}
        params = request.url.params
        split = lambda value: tuple(unquote(value).split(chr(31))) if value else ()  # noqa: E731
        if api == "v1":
            if rest == "/namespaces" and request.method == "GET":
                parent = split(params.get("parent"))
                if parent and parent not in catalog["namespaces"]:
                    raise PolarisError(404, "NoSuchNamespaceException", "Namespace does not exist")
                children = sorted(ns for ns in catalog["namespaces"] if len(ns) == len(parent) + 1
                                  and ns[:len(parent)] == parent)
                return httpx.Response(200, json={"namespaces": [list(ns) for ns in children]})
            if rest == "/namespaces" and request.method == "POST":
                namespace = tuple(body["namespace"])
                if namespace in catalog["namespaces"]:
                    raise PolarisError(409, "AlreadyExistsException", "Namespace already exists")
                catalog["namespaces"][namespace] = body.get("properties", {})
                return httpx.Response(200, json={"namespace": list(namespace)})
            match = re.match(r"^/namespaces/([^/]+)(/tables)?$", rest)
            if match:
                namespace = split(match.group(1))
                if namespace not in catalog["namespaces"]:
                    raise PolarisError(404, "NoSuchNamespaceException", "Namespace does not exist")
                if match.group(2):
                    names = sorted(catalog["tables"].get(namespace, ()))
                    return httpx.Response(200, json={"identifiers": [
                        {"namespace": list(namespace), "name": t} for t in names]})
                if request.method == "DELETE":
                    if catalog["tables"].get(namespace) or any(ns == namespace for ns, _ in catalog["policies"]):
                        raise PolarisError(409, "NamespaceNotEmptyException", "Namespace is not empty")
                    del catalog["namespaces"][namespace]
                    return httpx.Response(204)
            raise PolarisError(404, "NotFoundException", "Unknown path")
        if rest == "/applicable-policies":
            namespace = split(params.get("namespace"))
            found = self.applicable(db, list(namespace), params.get("target-name"))
            return httpx.Response(200, json={"applicable-policies": found, "next-page-token": None})
        match = re.match(r"^/namespaces/([^/]+)/policies(?:/([^/]+))?(/mappings)?$", rest)
        if not match:
            raise PolarisError(404, "NotFoundException", "Unknown path")
        namespace, name, mappings = split(match.group(1)), match.group(2), match.group(3)
        if namespace not in catalog["namespaces"]:
            raise PolarisError(404, "NoSuchNamespaceException", "Namespace does not exist")
        if name is None and request.method == "POST":
            if body.get("type") not in SYSTEM_TYPES or not re.match(r"^[A-Za-z0-9\-_]+$", body.get("name", "")):
                raise PolarisError(400, "BadRequestException", "Invalid policy")
            check_content(body["type"], body.get("content"))
            if (namespace, body["name"]) in catalog["policies"]:
                raise PolarisError(409, "AlreadyExistsException", "The given policy already exists")
            catalog["policies"][(namespace, body["name"])] = {"type": body["type"], "content": body.get("content"),
                                                              "description": body.get("description", ""), "version": 0}
            catalog["mappings"][(namespace, body["name"])] = set()
            return httpx.Response(200, json={"policy": self.public(namespace, body["name"], db)})
        if name is None:
            names = [n for ns, n in catalog["policies"] if ns == namespace]
            return httpx.Response(200, json={"identifiers": [{"namespace": list(namespace), "name": n} for n in names]})
        key = (namespace, name)
        if key not in catalog["policies"]:
            raise PolarisError(404, "NoSuchPolicyException", "The given policy does not exist")
        policy = catalog["policies"][key]
        if mappings and request.method == "PUT":
            target = self.target(db, body.get("target") or {})
            for other, other_policy in self.attached(db, target).items():
                if other != key and other_policy["type"] == policy["type"]:
                    raise PolarisError(409, "ConflictPolicyAttachmentException", "A policy of this type is attached")
            catalog["mappings"][key].add(target)
            return httpx.Response(204)
        if mappings and request.method == "POST":
            target = self.target(db, body.get("target") or {})
            if target not in catalog["mappings"][key]:
                raise PolarisError(404, "NoSuchMappingException", "The given mapping does not exist")
            catalog["mappings"][key].discard(target)
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(200, json={"policy": self.public(namespace, name, db)})
        if request.method == "PUT":
            if "current-policy-version" not in body:
                raise PolarisError(400, "BadRequestException", "current-policy-version is required")
            if body["current-policy-version"] != policy["version"]:
                raise PolarisError(409, "PolicyVersionMismatchException", "The policy version doesn't match")
            check_content(policy["type"], body.get("content", policy["content"]))
            policy["content"] = body.get("content", policy["content"])
            policy["version"] += 1
            return httpx.Response(200, json={"policy": self.public(namespace, name, db)})
        if request.method == "DELETE":
            if catalog["mappings"][key] and params.get("detach-all") != "true":
                raise PolarisError(400, "PolicyInUseException", "The policy is attached to one or more targets")
            del catalog["policies"][key]
            del catalog["mappings"][key]
            return httpx.Response(204)
        raise PolarisError(405, "MethodNotAllowed", "Unknown method")

    def public(self, namespace, name, db):
        policy = self.catalogs[db]["policies"][(namespace, name)]
        return {"policy-type": policy["type"], "inheritable": True, "name": name,
                "description": policy["description"], "content": policy["content"], "version": policy["version"]}

    # Shortcuts for tests: what another tool, such as Spark, would do through the same API.

    def put_policy(self, db, namespace, name, ptype, content, target):
        catalog = self.catalogs[db]
        catalog["namespaces"].setdefault(tuple(namespace), {})
        catalog["policies"][(tuple(namespace), name)] = {"type": ptype, "content": json.dumps(content),
                                                         "description": "", "version": 0}
        catalog["mappings"][(tuple(namespace), name)] = {self.target(db, target)}

    def content(self, db, namespace, name):
        return json.loads(self.catalogs[db]["policies"][(tuple(namespace), name)]["content"])


class FakeLauncher:
    """Answers the launcher protocol and writes a finished run's artifacts, as the Go runner would."""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.started = {}
        self.exit_code = 0
        self.fail_table = None
        self.hold = False
        self.killed = []

    @staticmethod
    def enabled(policies, key):
        return policies[key]["enabled"] and not policies[key]["problem"]

    def result(self, job):
        maintain, dry = job["kind"] == "maintain", job["dryRun"]
        tables = []
        for table in job["tables"]:
            name, policies = table["name"], table["policies"]
            small = 30 if name == READINGS else 0
            entry = {"name": name, "status": "success", "actions": [], "health": health(name, small=small, files=40,
                                                                                          expirable=2)}
            on = [key for key in policies if self.enabled(policies, key)]
            if maintain and not on:
                entry["status"] = "skipped"
                entry["actions"] = [{"operation": "compaction", "status": "off", "detail": "Off.",
                                     "policy": policies["dataCompaction"]["policy"]}]
            elif maintain:
                status = "planned" if dry else "done"
                compact = self.enabled(policies, "dataCompaction")
                entry["actions"] = [
                    {"operation": "compaction", "status": (status if small else "skipped") if compact else "off",
                     "detail": f"Rewrote {small} files into 1." if small else "No files need compaction.",
                     "metrics": {"inputFiles": small}, "policy": policies["dataCompaction"]["policy"]},
                    {"operation": "expireSnapshots", "status": status if self.enabled(policies, "snapshotExpiry")
                     else "off", "detail": "Expired 2 snapshots.", "metrics": {"snapshots": 2},
                     "policy": policies["snapshotExpiry"]["policy"]}]
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


def wire(app, polaris, launcher):
    """Point the app's Polaris client and launcher client at the fakes."""
    services = app.state.services
    services.polaris.http = httpx.AsyncClient(transport=httpx.MockTransport(polaris.handler))
    services.runs.http = httpx.AsyncClient(base_url="http://launcher", transport=httpx.MockTransport(launcher.handler))


def make_stack(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    config = settings(tmp_path, **overrides)
    bridge, verifier, polaris = FakeBridge(), FakeVerifier(), FakePolaris()
    launcher = FakeLauncher(tmp_path / "artifacts")
    app = create_app(config, bridge=bridge, verifier=verifier)
    wire(app, polaris, launcher)
    return config, bridge, polaris, launcher, app


@pytest.fixture
def stack(tmp_path, monkeypatch):
    config, bridge, polaris, launcher, app = make_stack(tmp_path, monkeypatch)
    with TestClient(app) as client:
        def call(method, path, user="alice", **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {user}"}, **kwargs)

        def run(mode="inspect", user="bob", database=DEV, **fields):
            return call("POST", f"/databases/{database}/runs", user, json={"mode": mode, "wait": 5, **fields})

        def tool(name, user="alice", **arguments):
            """Call an MCP tool as a signed-in agent would, in the app's event loop; its result as a dict."""
            return client.portal.call(_call_tool, app, user, name, arguments)

        yield SimpleNamespace(client=client, call=call, run=run, tool=tool, bridge=bridge, launcher=launcher,
                              polaris=polaris, app=app, services=app.state.services, settings=config)


async def _call_tool(app, user, name, arguments):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from maintenance.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    access = AccessToken(token=user, client_id="ext-maintenance-mcp", scopes=[], expires_at=4102444800,
                         subject=f"sub-{user}", claims={"name": user})
    signed_in = auth_context_var.set(AuthenticatedUser(access))
    try:
        return (await mcp.call_tool(name, arguments)).structured_content
    finally:
        auth_context_var.reset(signed_in)


def _fast_sleep(original):
    async def sleep(delay, *args, **kwargs):
        return await original(min(delay, 0.01), *args, **kwargs)
    return sleep
