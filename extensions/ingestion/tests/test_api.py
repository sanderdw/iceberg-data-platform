import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ingestion import templates, validate
from ingestion.api import OPERATIONS
from ingestion.bridge_client import in_range
from ingestion.config import ROOT, contract_range
from ingestion.errors import IngestionError
from tests.conftest import DATABASES, OTHER, REST, TEAM, make_stack

SECRET = "sk-live-very-secret-value"


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def ready(stack, **fields):
    """An enabled environment and a pipeline with its secret set."""
    enable(stack)
    pipeline = stack.create(**fields).json()
    assert stack.call("PUT", f"/pipelines/{pipeline['id']}/secrets", json={"secrets": {"token": SECRET}}).status_code \
        == 200
    return pipeline


def test_whoami_and_environments(stack):
    me = stack.call("GET", "/me").json()
    assert me["extension"] == "ingestion" and me["sourceTypes"] == ["rest_api", "sql_database", "filesystem"]
    assert stack.call("POST", f"/teams/{TEAM}/environments/development", "carol").json()["code"] == "forbidden_role"
    enable(stack)
    envs = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert envs[0]["enabled"] and {d["name"] for d in envs[0]["databases"]} == {"sales", "raw-data"}
    assert stack.call("GET", f"/teams/{TEAM}/environments", "dave").json()["code"] == "not_a_member"


def test_only_administrators_create_pipelines(stack):
    for user in ("bob", "carol", "dave"):
        assert stack.create(user).json()["code"] == "forbidden_role"
    created = stack.create()
    assert created.status_code == 201
    body = created.json()
    assert body["database"] == DATABASES[("sales", "development")] and body["databaseName"] == "sales"
    assert body["secretsMissing"] == ["token"] and body["secretsSet"] == [] and "set_pipeline_secrets" in body["hint"]
    assert body["resources"]["customers"] == {"writeDisposition": "merge", "primaryKey": ["id"], "partition": []}
    assert stack.create().json()["code"] == "name_taken"
    assert stack.create(name="x", database="db-" + "2" * 32).json()["code"] == "unknown_database"


@pytest.mark.parametrize(("source", "field"), [
    ({**REST, "client": {**REST["client"], "auth": {"type": "bearer", "token": "plain"}}}, "source.client.auth.token"),
    ({**REST, "resources": [{"name": "x", "endpoint": {"path": "https://evil.example.com/x"}}]},
     "source.resources[0].endpoint.path"),
    ({**REST, "client": {**REST["client"], "base_url": "http://169.254.169.254/latest/"}}, "source.client.base_url"),
    ({**REST, "client": {**REST["client"], "base_url": "https://intranet.example.com/"}}, "source.client.base_url"),
    ({**REST, "client": {**REST["client"], "base_url": "https://localhost/"}}, "source.client.base_url"),
    ({**REST, "client": {**REST["client"], "base_url": "file:///etc/passwd"}}, "source.client.base_url"),
    ({**REST, "destination": "filesystem"}, "source"),
    ({**REST, "client": {**REST["client"], "session": {}}}, "source.client"),
    ({**REST, "client": {**REST["client"], "base_url": "https://user:pw@api.example.com/"}}, "source.client.base_url"),
])
def test_definitions_are_checked(stack, source, field):
    response = stack.call("POST", "/pipelines/validate", json={"sourceType": "rest_api", "source": source})
    assert response.status_code == 422 and response.json()["detail"]["field"] == field, response.json()


def test_sql_and_files_are_checked(stack):
    def check(source_type, source, resources=None):
        return stack.call("POST", "/pipelines/validate",
                          json={"sourceType": source_type, "source": source, "resources": resources})
    db = {"dialect": "postgresql", "host": "db.example.com", "database": "crm", "username": "reader",
          "password": "$secret:password", "tables": ["customers"]}
    ok = check("sql_database", db, {"customers": {"writeDisposition": "merge", "primaryKey": "id"}}).json()
    assert ok["valid"] and ok["secretsNeeded"] == ["password"] and ok["resourceNames"] == ["customers"]
    assert ok["source"]["tables"] == [{"name": "customers"}]
    assert check("sql_database", {**db, "password": "hunter2"}).json()["detail"]["field"] == "source.password"
    assert check("sql_database", {**db, "dialect": "sqlite"}).json()["detail"]["field"] == "source.dialect"
    assert check("sql_database", {**db, "query": {"local_infile": "1"}}).json()["code"] == "invalid_pipeline"
    assert check("sql_database", {**db, "host": "10.1.2.3"}).json()["detail"]["field"] == "source.host"
    assert check("sql_database", db, {"customers": {"writeDisposition": "merge"}}).json()["code"] == "invalid_pipeline"
    assert check("sql_database", db, {"orders": {}}).json()["detail"]["field"] == "resources"
    files = {"url": "https://data.example.com/a.csv", "format": "csv", "table": "a"}
    assert check("filesystem", files).json()["valid"]
    assert check("filesystem", {**files, "file_glob": "*.csv"}).json()["detail"]["field"] == "source.file_glob"
    s3 = {"url": "s3://bucket/in/", "format": "parquet", "table": "a", "file_glob": "../*",
          "s3": {"region": "eu-west-1", "access_key_id": "AK", "secret_access_key": "$secret:s3"}}
    assert check("filesystem", s3).json()["detail"]["field"] == "source.file_glob"
    assert check("filesystem", {**s3, "file_glob": "*.parquet", "incremental": True}).json()["valid"]


def test_private_sources_need_the_platforms_permission(tmp_path, monkeypatch):
    _, _, _, app = make_stack(tmp_path, monkeypatch, allow_private=True)
    with TestClient(app) as client:
        source = {**REST, "client": {**REST["client"], "base_url": "http://host.docker.internal:8000/"}}
        response = client.post("/api/v1/pipelines/validate", headers={"Authorization": "Bearer alice"},
                               json={"sourceType": "rest_api", "source": source})
        assert response.json()["valid"]


def test_every_template_is_a_valid_pipeline():
    for template in templates.TEMPLATES:
        checked = validate.definition(template["sourceType"], template["source"], template["resources"],
                                      resolve=False)
        assert checked["secrets"] == template["secrets"], template["id"]


def test_secrets_are_write_only_and_encrypted(stack):
    pipeline = stack.create().json()
    path = f"/pipelines/{pipeline['id']}/secrets"
    assert stack.call("PUT", path, "bob", json={"secrets": {"token": SECRET}}).json()["code"] == "forbidden_role"
    assert stack.call("PUT", path, json={"secrets": {"other": SECRET}}).json()["code"] == "invalid_pipeline"
    response = stack.call("PUT", path, json={"secrets": {"token": SECRET}})
    assert response.json()["secretsSet"] == ["token"] and response.json()["secretsMissing"] == []
    for text in (response.text, stack.call("GET", f"/pipelines/{pipeline['id']}").text,
                 stack.call("GET", "/pipelines").text):
        assert SECRET not in text
    database = sqlite3.connect(stack.settings.state_dir / "state.sqlite3")
    raw = database.execute("SELECT secrets FROM pipelines").fetchone()
    assert SECRET not in raw[0] and "token" in json.loads(raw[0])
    removed = stack.call("PUT", path, json={"secrets": {"token": None}}).json()
    assert removed["secretsMissing"] == ["token"]


def test_runs_execute_as_the_team_with_state_between_runs(stack):
    pipeline = stack.create().json()
    path = f"/pipelines/{pipeline['id']}/runs"
    assert stack.call("POST", path, "bob").json()["code"] == "secrets_missing"
    stack.call("PUT", f"/pipelines/{pipeline['id']}/secrets", json={"secrets": {"token": SECRET}})
    assert stack.call("POST", path, "bob").json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.call("POST", path, "carol").json()["code"] == "forbidden_role"
    first = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert first["status"] == "success" and first["triggeredBy"] == "bob"
    assert first["summary"]["tables"] == [{"name": "customers", "rows": 3}] and first["summary"]["rows"] == 3
    started = stack.launcher.started[first["id"]]
    assert started["token"] == "token-write" and started["secrets"] == {"token": SECRET}
    assert stack.bridge.tokens[-1][1] == "write" and started["state"] is None
    job = started["job"]
    assert job["warehouse"] == DATABASES[("sales", "development")] and job["namespace"] == "raw_crm"
    assert job["url"] == f"http://localhost:3008/#/pipelines/{pipeline['id']}" and not job["allowPrivate"]
    assert job["source"]["client"]["auth"]["token"] == "$secret:token" and SECRET not in json.dumps(job)
    assert job["catalogUri"] == "http://polaris-control-plane:8181/api/catalog"
    second = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[second["id"]]["state"] == {"runs": 1}
    third = stack.call("POST", path, "bob", json={"wait": 30, "full_refresh": True}).json()
    assert stack.launcher.started[third["id"]]["state"] is None and third["fullRefresh"]
    runs = stack.call("GET", f"{path}?limit=2").json()
    assert [r["id"] for r in runs] == [third["id"], second["id"]]
    assert stack.call("GET", f"/pipelines/{pipeline['id']}").json()["lastRun"]["id"] == third["id"]


def test_a_failed_run_keeps_the_previous_state(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}/runs"
    stack.call("POST", path, "bob", json={"wait": 30})
    stack.launcher.exit_code = 1
    failed = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert failed["status"] == "failed" and failed["error"] == "Source returned 401"
    stack.launcher.exit_code = 0
    after = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[after["id"]]["state"] == {"runs": 1}


def test_logs_mask_secrets(stack):
    pipeline = ready(stack)
    stack.launcher.log = f"GET https://api.example.com/v1/customers with {SECRET}\nAuthorization: Bearer abcdefghijk\n"
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    lines = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()["lines"]
    assert SECRET not in "\n".join(lines) and "abcdefghijk" not in "\n".join(lines) and len(lines) == 2


def test_one_run_per_pipeline_and_cancel(stack):
    pipeline = ready(stack)
    stack.launcher.hold = True
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob").json()
    for _ in range(100):
        status = stack.call("GET", f"/runs/{run['id']}").json()["status"]
        if status == "running" and run["id"] in stack.launcher.started:
            break
        asyncio.run(asyncio.sleep(0))
    assert stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob").json()["code"] == "run_in_progress"
    assert stack.call("PATCH", f"/pipelines/{pipeline['id']}", json={"name": "x"}).json()["code"] == "run_in_progress"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").json()["cancelling"]
    done = stack.services.runs.wait(run["id"], 10)
    assert stack.client.portal.call(lambda: done)["status"] == "cancelled"


def test_changing_where_a_pipeline_connects_removes_its_secrets(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}"
    stack.call("POST", f"{path}/runs", "bob", json={"wait": 30})
    renamed = stack.call("PATCH", path, json={"name": "crm api", "expected_revision": 2}).json()
    assert renamed["name"] == "crm api" and renamed["secretsSet"] == ["token"] and "hint" not in renamed
    stale = stack.call("PATCH", path, json={"description": "x", "expected_revision": 2}).json()
    assert stale["code"] == "revision_conflict"
    moved = stack.call("PATCH", path, json={"source": {**REST, "client": {**REST["client"],
                                                                           "base_url": "https://evil.example.org/"}}})
    assert moved.json()["secretsMissing"] == ["token"] and "secrets were removed" in moved.json()["hint"]
    assert stack.call("PATCH", path, "bob", json={"name": "y"}).json()["code"] == "forbidden_role"
    elsewhere = stack.call("PATCH", path, json={"namespace": "raw_crm_v2"}).json()
    assert "from scratch" in elsewhere["hint"] and stack.services.store.state(pipeline["id"]) is None


def test_delete_needs_the_name_and_keeps_tables(stack):
    pipeline = ready(stack)
    stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30})
    path = f"/pipelines/{pipeline['id']}/delete"
    assert stack.call("POST", path, json={"confirm_name": "nope"}).json()["code"] == "confirmation_mismatch"
    assert stack.call("POST", path, "bob", json={"confirm_name": "crm"}).json()["code"] == "forbidden_role"
    deleted = stack.call("POST", path, json={"confirm_name": "crm"}).json()
    assert deleted["deleted"] and "stay" in deleted["note"]
    assert stack.call("GET", f"/pipelines/{pipeline['id']}").json()["code"] == "unknown_pipeline"


def test_other_teams_see_nothing(stack):
    pipeline = ready(stack)
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.call("GET", "/pipelines", "dave").json() == []
    assert stack.call("GET", f"/pipelines/{pipeline['id']}", "dave").json()["code"] == "unknown_pipeline"
    assert stack.call("GET", f"/runs/{run['id']}", "dave").json()["code"] == "unknown_run"
    assert stack.call("GET", f"/runs/{run['id']}/logs", "dave").json()["code"] == "unknown_run"


def test_schedules_never_overlap(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}/schedule"
    assert stack.call("PUT", path, json={"cron": "* * * * *"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, json={"cron": "not cron"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, "bob", json={"cron": "0 5 * * *"}).json()["code"] == "forbidden_role"
    schedule = stack.call("PUT", path, json={"cron": "0 5 * * *"}).json()
    assert schedule["cron"] == "0 5 * * *" and schedule["enabled"]
    scheduler_tick = __import__("ingestion.scheduler", fromlist=["Scheduler"]).Scheduler(stack.services).tick
    stack.launcher.hold = True
    started = stack.client.portal.call(scheduler_tick, schedule["nextRunAt"] + 1)
    assert len(started) == 1
    again = stack.client.portal.call(scheduler_tick, schedule["nextRunAt"] + 86400 + 1)
    assert again == []
    run = stack.services.store.run(started[0])
    assert run["triggered_by"] == "schedule" and run["schedule"] == "0 5 * * *"
    assert stack.call("DELETE", path).json()["deleted"]
    assert stack.call("DELETE", path).json()["code"] == "no_schedule"


def test_runs_stop_before_the_token_expires(stack):
    pipeline = ready(stack)
    stack.bridge.token_lifetime = 3600
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.launcher.started[run["id"]]["timeout"] == 2700
    stack.bridge.token_lifetime = 1000
    short = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    assert short["status"] == "error" and "POLARIS_TOKEN_LIFETIME" in short["error"]


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.post("/api/v1/pipelines/validate", json={})
    assert response.json()["code"] == "csrf"


def test_secrets_need_a_key(tmp_path):
    from ingestion.secrets import Secrets
    with pytest.raises(IngestionError) as exc:
        Secrets("").seal("token", "x")
    assert exc.value.code == "secrets_unavailable"
    sealed = Secrets("ab" * 32).seal("token", "value")
    assert Secrets("ab" * 32).open("token", sealed) == "value"
    with pytest.raises(IngestionError):
        Secrets("cd" * 32).open("token", sealed)
    with pytest.raises(IngestionError):
        Secrets("ab" * 32).open("password", sealed)


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = stack.client.portal.call(_tools, stack.app)
    assert set(OPERATIONS.values()) == mcp
    contract = ROOT / "contracts" / "ingestion-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/ingestion/contracts/ingestion-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


async def _tools(app):
    from ingestion.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name for tool in await mcp.list_tools()}


def test_supports_the_platforms_bridge_contract():
    supported = contract_range()
    assert in_range("0.1.0", supported) and in_range("0.1.4", supported)
    assert not in_range("0.2.0", supported) and not in_range("1.0.0", supported)


def test_other_team_cannot_create_in_a_foreign_team(stack):
    assert stack.create("dave").json()["code"] == "forbidden_role"
    assert stack.call("POST", "/pipelines", "dave", json={
        "team": OTHER, "name": "x", "database": "sales", "namespace": "raw", "sourceType": "rest_api",
        "source": REST}).json()["code"] == "unknown_database"
