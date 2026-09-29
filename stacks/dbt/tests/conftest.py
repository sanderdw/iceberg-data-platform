"""Fakes for the platform side: the Bridge, Keycloak tokens and the launcher.

The launcher fake finishes a run immediately and writes the artifacts a real runner would:
result.json, run_results.json and the Parquet information schema for the starter project.
"""

import asyncio
import base64
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from dbt_portal.app import create_app
from dbt_portal.auth import Caller
from dbt_portal.config import Settings
from dbt_portal.errors import DbtError

TEAM = "team-" + "a" * 32
OTHER = "team-" + "b" * 32
ISSUER = "http://localhost:8080/realms/iceberg"
DATABASES = {
    ("sales", "development"): "db-" + "1" * 32, ("sales", "production"): "db-" + "2" * 32,
    ("raw-data", "development"): "db-" + "3" * 32,
}


def settings(tmp_path):
    return Settings(bridge_url="http://bridge:3005", extension_id="dbt", client_id="ext-dbt", client_secret="s" * 48,
                    mcp_client_id="ext-dbt-mcp", issuer=ISSUER, internal_issuer=ISSUER,
                    origin="http://localhost:3004", state_dir=Path(tmp_path), launcher_url="http://launcher",
                    launcher_token="l" * 40)


class FakeBridge:
    def __init__(self):
        self.roles = {"alice": {TEAM: "admin"}, "bob": {TEAM: "writer"}, "carol": {TEAM: "reader"},
                      "dave": {OTHER: "admin"}}
        self.automation = []
        self.tokens = []

    async def discovery(self, refresh=False):
        return {"contractVersion": "1.0.0",
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
            raise DbtError(403, "Only team administrators can enable an extension.", "forbidden_role")
        item = {"id": "svc-" + str(len(self.automation)).rjust(32, "0"), "team": team, "environment": environment,
                "extension": "dbt", "status": "active", "createdBy": token}
        self.automation.append(item)
        return item

    async def revoke(self, token, id):
        self.automation = [a for a in self.automation if a["id"] != id]
        return {"revoked": True}

    async def principal_for(self, team, environment):
        for item in self.automation:
            if item["team"] == team and item["environment"] == environment:
                return item
        raise DbtError(409, "dbt is not enabled.", "environment_not_enabled")

    async def scope(self, id):
        item = next(a for a in self.automation if a["id"] == id)
        return {**item, "databases": [
            {"id": db, "name": name, "environment": env, "team": TEAM, "status": "ready", "storageLocation": "s3://x/"}
            for (name, env), db in DATABASES.items() if env == item["environment"]]}

    async def catalog_token(self, id, access, purpose):
        self.tokens.append((id, access, purpose))
        return {"accessToken": f"token-{access}", "expiresIn": 3600}


class FakeVerifier:
    async def verify(self, token):
        if token not in ("alice", "bob", "carol", "dave"):
            raise DbtError(401, "Sign in again.", "unauthorized")
        return Caller(token, "sub-" + token, token, "ext-dbt-mcp", 4102444800.0)


def write_info_schema(directory, project="shop"):
    """A small but real-shaped dbt information schema: seed → staging → mart, with tests."""
    directory.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    relation = ("unique_id VARCHAR, name VARCHAR, description VARCHAR, database_name VARCHAR, schema_name VARCHAR, "
                "identifier VARCHAR, alias VARCHAR, materialized VARCHAR, tags VARCHAR[], original_file_path VARCHAR, "
                "raw_code VARCHAR, compiled_code VARCHAR, package_name VARCHAR")

    def write(name, schema, rows):
        connection.execute(f"CREATE OR REPLACE TABLE t ({schema})")
        if rows:
            connection.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(rows[0]))})", rows)
        connection.execute(f"COPY t TO '{directory / (name + '.parquet')}' (FORMAT parquet)")

    write("dbt.seeds", relation, [(f"seed.{project}.regions", "regions", "Regions", "sales", "seeds", "regions",
                                   "regions", "seed", [], "seeds/regions.csv", "", "", project)])
    write("dbt.models", relation, [
        (f"model.{project}.stg_regions", "stg_regions", "Cleaned regions", "sales", "staging", "stg_regions",
         "stg_regions", "replace_table", ["daily"], "models/staging/stg_regions.sql", "select 1",
         'select 1 from "sales"."seeds"."regions"', project),
        (f"model.{project}.regions_per_country", "regions_per_country", "Per country", "sales", "marts",
         "regions_per_country", "regions_per_country", "replace_table", [], "models/marts/regions_per_country.sql",
         "select 2", "select 2", project),
    ])
    write("dbt.sources", relation, [(f"source.{project}.raw.events", "events", "", "raw_data", "raw", "events",
                                     "events", None, [], "models/sources.yml", "", "", project)])
    write("dbt.edges", "parent_unique_id VARCHAR, child_unique_id VARCHAR", [
        (f"seed.{project}.regions", f"model.{project}.stg_regions"),
        (f"source.{project}.raw.events", f"model.{project}.stg_regions"),
        (f"model.{project}.stg_regions", f"model.{project}.regions_per_country"),
        ("macro.dbt.ref", f"model.{project}.stg_regions"),
        (f"model.{project}.stg_regions", f"test.{project}.unique_stg_regions_region_id"),
    ])
    write("dbt.node_columns", "node_unique_id VARCHAR, column_name VARCHAR, column_index BIGINT, data_type VARCHAR, "
                              "description VARCHAR", [
        (f"model.{project}.stg_regions", "region_id", 0, "INTEGER", "Stable id"),
        (f"model.{project}.stg_regions", "country_code", 1, "VARCHAR", ""),
    ])
    write("dbt.data_tests", "unique_id VARCHAR, name VARCHAR, node_unique_id VARCHAR, column_name VARCHAR, "
                            "test_name VARCHAR, severity VARCHAR", [
        (f"test.{project}.unique_stg_regions_region_id", "unique_stg_regions_region_id",
         f"model.{project}.stg_regions", "region_id", "unique", "ERROR"),
    ])
    write("dbt.project", "project_name VARCHAR, dbt_version VARCHAR", [(project, "2.0.5")])
    write("dbt.dag_nodes", "unique_id VARCHAR, resource_type VARCHAR", [(f"model.{project}.stg_regions", "model")])


class FakeLauncher:
    """Answers the launcher protocol and writes a finished run's artifacts."""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.started = {}
        self.exit_code = 0

    def handler(self, request):
        if request.method == "POST" and request.url.path == "/runs":
            body = json.loads(request.content)
            staged = tarfile.open(fileobj=io.BytesIO(base64.b64decode(body["archive"])))
            job = json.load(staged.extractfile("job.json"))
            self.started[body["id"]] = {"token": body["token"], "job": job, "files": staged.getnames(),
                                        "staged": staged}
            out = self.artifacts / body["id"] / "out"
            (out / "target").mkdir(parents=True)
            project = "shop"
            results = [{"unique_id": f"model.{project}.stg_regions", "status": "success", "execution_time": 0.2,
                        "adapter_response": {"rows_affected": 3}},
                       {"unique_id": f"test.{project}.unique_stg_regions_region_id", "status": "pass"}]
            if job["command"][0] in ("build", "run", "seed", "test", "compile", "parse"):
                (out / "target" / "run_results.json").write_text(json.dumps({"results": results}))
                write_info_schema(out / "target" / "info_schema" / "v1")
            if job["command"][0] == "show":
                (out / "show.json").write_text(json.dumps([{"region_id": 1}]))
            (out / "dbt.log").write_text("Finished 'build' successfully\n")
            (out / "result.json").write_text(json.dumps({"exitCode": self.exit_code}))
            return httpx.Response(202, json={"id": body["id"], "state": "running"})
        if request.method == "GET" and request.url.path.startswith("/runs/"):
            return httpx.Response(200, json={"state": "finished", "exitCode": self.exit_code, "timedOut": False})
        if request.method == "DELETE":
            return httpx.Response(200, json={"cancelled": True})
        return httpx.Response(404, json={"error": "unknown"})


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    config = settings(tmp_path)
    bridge, verifier = FakeBridge(), FakeVerifier()
    launcher = FakeLauncher(tmp_path / "artifacts")
    app = create_app(config, bridge=bridge, verifier=verifier)
    runs = app.state.services.runs
    runs.http = httpx.AsyncClient(base_url="http://launcher", transport=httpx.MockTransport(launcher.handler))
    with TestClient(app) as client:
        def call(method, path, user="alice", **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {user}"}, **kwargs)

        yield SimpleNamespace(client=client, call=call, bridge=bridge, launcher=launcher, app=app,
                              services=app.state.services, settings=config)


def _fast_sleep(original):
    async def sleep(delay, *args, **kwargs):
        # Polling loops finish at once; the scheduler's long sleep still yields.
        return await original(min(delay, 0.01), *args, **kwargs)
    return sleep
