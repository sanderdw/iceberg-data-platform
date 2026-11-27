import asyncio
import json
import re
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ingestion import templates, validate
from ingestion.api import OPERATIONS
from ingestion.bridge_client import in_range
from ingestion.config import ROOT, contract_range
from ingestion.errors import IngestionError
from tests.conftest import DATABASES, OTHER, REST, TEAM, ToolRefused, make_stack

SECRET = "sk-live-very-secret-value"
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
RUN_FIELDS = {"id", "pipeline", "environment", "status", "triggeredBy", "agent", "scheduled", "revision",
              "fullRefresh", "createdAt", "startedAt", "finishedAt", "exitCode", "summary", "error"}


def seconds(timestamp):
    assert TIMESTAMP.match(timestamp), timestamp
    return datetime.fromisoformat(timestamp).timestamp()


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def ready(stack, **fields):
    """An enabled environment and a pipeline with its secret set."""
    enable(stack)
    pipeline = stack.create(**fields).json()
    assert stack.call("PATCH", f"/pipelines/{pipeline['id']}/secrets",
                      json={"secrets": {"token": SECRET}}).status_code == 200
    return pipeline


def test_whoami_and_environments(stack):
    me = stack.call("GET", "/me").json()
    # Built field by field: nothing else the Bridge says about the caller (such as sharedDatabases) comes along.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "sourceTypes", "allowPrivateSources"}
    assert me["user"] == {"id": "portal-alice" + "0" * 27, "name": "alice"}
    assert me["memberships"] == [{"team": TEAM, "teamName": TEAM[:9], "role": "admin"}]
    assert {"id": DATABASES[("sales", "development")], "name": "sales", "team": TEAM, "environment": "development",
            "status": "ready"} in me["databases"]
    assert me["extension"] == "ingestion" and me["sourceTypes"] == ["rest_api", "sql_database", "filesystem"]
    assert me["allowPrivateSources"] is False and me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
    assert me["apps"]["workspace"] == [{"id": "portal", "title": "User portal", "url": "http://localhost:3002"}]
    path = f"/teams/{TEAM}/environments"
    assert stack.call("POST", f"{path}/development", "carol").json()["code"] == "forbidden_role"
    databases = [{"id": DATABASES[("sales", "development")], "name": "sales"},
                 {"id": DATABASES[("raw-data", "development")], "name": "raw-data"}]
    enabled = stack.call("POST", f"{path}/development")
    assert enabled.status_code == 200
    assert enabled.json() == {"team": TEAM, "environment": "development", "enabled": True, "enabledBy": "alice",
                              "databases": databases}
    envs = stack.call("GET", path, "carol").json()
    assert list(envs) == ["environments"] and envs["environments"][0] == enabled.json()
    assert envs["environments"][2] == {"team": TEAM, "environment": "production", "enabled": False,
                                       "enabledBy": None, "databases": [databases[0] | {
                                           "id": DATABASES[("sales", "production")]}]}
    assert stack.call("GET", "/me").json()["enabledEnvironments"] == [{"team": TEAM, "environments": ["development"]}]
    assert stack.call("GET", path, "dave").json()["code"] == "not_a_member"
    disabled = stack.call("DELETE", f"{path}/development")
    assert disabled.status_code == 200
    assert disabled.json() == {**enabled.json(), "enabled": False, "enabledBy": None}
    again = stack.call("DELETE", f"{path}/development")
    assert again.status_code == 409 and again.json()["code"] == "environment_not_enabled"


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
    # A database named in the body that is not the team's in this environment: the field is wrong, not the path.
    elsewhere = stack.create(name="x", database="db-" + "2" * 32)
    assert elsewhere.status_code == 422 and elsewhere.json()["code"] == "invalid_database"
    assert TIMESTAMP.match(body["createdAt"]) and body["updatedAt"] == body["createdAt"]
    assert body["schedule"] is None and body["lastRun"] is None


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
    sts = {**s3, "file_glob": "*.parquet", "s3": {**s3["s3"], "session_token": "$secret:s3_token"}}
    assert check("filesystem", sts).json()["secretsNeeded"] == ["s3", "s3_token"]
    assert check("filesystem", {**sts, "s3": {**sts["s3"], "session_token": "IQo"}}).json()["detail"]["field"] == (
        "source.s3.session_token")
    anonymous = {**sts, "s3": {"region": "eu-west-1", "session_token": "$secret:s3_token"}}
    assert check("filesystem", anonymous).json()["detail"]["field"] == "source.s3.session_token"


def test_a_file_url_can_be_a_secret(stack):
    enable(stack)
    files = {"url": "$secret:file_url", "format": "parquet", "table": "areas"}
    pipeline = stack.create(sourceType="filesystem", source=files, resources={}).json()
    assert pipeline["secretsMissing"] == ["file_url"]
    path = f"/pipelines/{pipeline['id']}/secrets"
    signed = "https://bucket.s3.eu-west-1.amazonaws.com/areas.parquet?X-Amz-Expires=600&X-Amz-Signature=" + "ab" * 1200
    for bad in ("file:///etc/passwd", "s3://bucket/areas.parquet", "http://10.1.2.3/areas.parquet"):
        refused = stack.call("PATCH", path, json={"secrets": {"file_url": bad}}).json()
        assert refused["code"] == "invalid_pipeline" and refused["detail"]["field"] == "secrets.file_url", bad
    set_ = stack.call("PATCH", path, json={"secrets": {"file_url": signed}})
    assert set_.json()["secretsSet"] == ["file_url"] and "X-Amz-Signature" not in set_.text
    assert "X-Amz" not in stack.call("GET", f"/pipelines/{pipeline['id']}").text


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
    assert stack.call("PATCH", path, "bob", json={"secrets": {"token": SECRET}}).json()["code"] == "forbidden_role"
    assert stack.call("PATCH", path, json={"secrets": {"other": SECRET}}).json()["code"] == "invalid_pipeline"
    # The log scrubbers do not mask values this short.
    assert stack.call("PATCH", path, json={"secrets": {"token": "abc"}}).json()["code"] == "invalid_pipeline"
    # The call merges, so it is a PATCH; PUT is gone.
    assert stack.call("PUT", path, json={"secrets": {"token": SECRET}}).status_code == 405
    response = stack.call("PATCH", path, json={"secrets": {"token": SECRET}})
    assert response.json()["secretsSet"] == ["token"] and response.json()["secretsMissing"] == []
    for text in (response.text, stack.call("GET", f"/pipelines/{pipeline['id']}").text,
                 stack.call("GET", "/pipelines").text):
        assert SECRET not in text
    database = sqlite3.connect(stack.settings.state_dir / "state.sqlite3")
    raw = database.execute("SELECT secrets FROM pipelines").fetchone()
    assert SECRET not in raw[0] and "token" in json.loads(raw[0])
    removed = stack.call("PATCH", path, json={"secrets": {"token": None}}).json()
    assert removed["secretsMissing"] == ["token"]


def test_runs_execute_as_the_team_with_state_between_runs(stack):
    pipeline = stack.create().json()
    path = f"/pipelines/{pipeline['id']}/runs"
    assert stack.call("POST", path, "bob").json()["code"] == "secrets_missing"
    stack.call("PATCH", f"/pipelines/{pipeline['id']}/secrets", json={"secrets": {"token": SECRET}})
    not_enabled = stack.call("POST", path, "bob")
    assert not_enabled.status_code == 409 and not_enabled.json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.call("POST", path, "carol").json()["code"] == "forbidden_role"
    started = stack.call("POST", path, "bob", json={"wait": 30})
    assert started.status_code == 202
    first = started.json()
    assert set(first) == RUN_FIELDS | {"results"}
    assert first["status"] == "success" and first["triggeredBy"] == "bob" and first["scheduled"] is False
    assert first["agent"] == "ext-ingestion-mcp" and first["error"] is None and first["exitCode"] == 0
    # The tables are the run's results, beside its totals; the account it ran as is not shown.
    assert first["results"] == [{"name": "customers", "rows": 3}]
    assert first["summary"]["rows"] == 3 and not {"tables", "principal"} & set(first["summary"])
    assert first["summary"]["error"] is None
    assert seconds(first["createdAt"]) <= seconds(first["startedAt"]) <= seconds(first["finishedAt"])
    assert stack.call("GET", f"/runs/{first['id']}", "carol").json() == first
    started = stack.launcher.started[first["id"]]
    assert started["token"] == "token-write" and started["secrets"] == {"token": SECRET}
    assert stack.bridge.tokens[-1][1] == "write" and started["state"] is None
    job = started["job"]
    assert job["warehouse"] == DATABASES[("sales", "development")] and job["namespace"] == "raw_crm"
    assert job["url"] == f"http://localhost:3008/#/pipelines/{pipeline['id']}" and not job["allowPrivate"]
    assert job["maxDownload"] == 4 * 1024**3
    assert job["source"]["client"]["auth"]["token"] == "$secret:token" and SECRET not in json.dumps(job)
    assert job["catalogUri"] == "http://polaris-control-plane:8181/api/catalog"
    second = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[second["id"]]["state"] == {"runs": 1}
    old_name = stack.call("POST", path, "bob", json={"full_refresh": True})
    assert old_name.status_code == 422 and old_name.json()["code"] == "invalid_request"
    third = stack.call("POST", path, "bob", json={"wait": 30, "fullRefresh": True}).json()
    assert stack.launcher.started[third["id"]]["state"] is None and third["fullRefresh"]
    runs = stack.call("GET", f"{path}?limit=2").json()
    assert list(runs) == ["runs"] and [r["id"] for r in runs["runs"]] == [third["id"], second["id"]]
    # A list never carries the per-table results.
    assert all(set(r) == RUN_FIELDS for r in runs["runs"])
    for limit in (0, 201):
        refused = stack.call("GET", f"{path}?limit={limit}")
        assert refused.status_code == 422 and refused.json()["code"] == "invalid_request"
    detail = stack.call("GET", f"/pipelines/{pipeline['id']}").json()
    assert detail["lastRun"]["id"] == third["id"] and set(detail["lastRun"]) == RUN_FIELDS
    assert [r["id"] for r in detail["recentRuns"]] == [third["id"], second["id"], first["id"]]
    assert all(set(r) == RUN_FIELDS for r in detail["recentRuns"])


def test_a_failed_run_keeps_the_previous_state(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}/runs"
    stack.call("POST", path, "bob", json={"wait": 30})
    stack.launcher.exit_code = 1
    failed = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert failed["status"] == "failed" and failed["error"] == "Source returned 401"
    assert failed["summary"]["error"] == "Source returned 401" and failed["exitCode"] == 1
    stack.launcher.exit_code = 137
    killed = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert killed["status"] == "error" and "out of memory" in killed["error"] and killed["exitCode"] == 137
    stack.launcher.exit_code = 0
    after = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[after["id"]]["state"] == {"runs": 1}


def test_logs_mask_secrets(stack):
    pipeline = ready(stack)
    stack.launcher.log = f"GET https://api.example.com/v1/customers with {SECRET}\nAuthorization: Bearer abcdefghijk\n"
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    logs = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()
    lines = logs["lines"]
    assert set(logs) == {"run", "status", "lines"} and logs["run"] == run["id"] and logs["status"] == "success"
    assert SECRET not in "\n".join(lines) and "abcdefghijk" not in "\n".join(lines) and len(lines) == 2
    # limit gives the last lines; outside 1 to 5000 it is refused, never clamped.
    last = stack.call("GET", f"/runs/{run['id']}/logs?limit=1", "carol").json()["lines"]
    assert last == lines[-1:]
    for limit in (0, 5001):
        refused = stack.call("GET", f"/runs/{run['id']}/logs?limit={limit}", "carol")
        assert refused.status_code == 422 and refused.json()["code"] == "invalid_request"


def test_one_run_per_pipeline_and_cancel(stack):
    pipeline = ready(stack)
    stack.launcher.hold = True
    started = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob")
    run = started.json()
    # Without wait the answer is the run as it is now, in the same shape as a finished one.
    assert started.status_code == 202 and run["status"] == "queued" and run["results"] == []
    assert run["startedAt"] is None and run["finishedAt"] is None and run["exitCode"] is None
    for _ in range(100):
        status = stack.call("GET", f"/runs/{run['id']}").json()["status"]
        if status == "running" and run["id"] in stack.launcher.started:
            break
        asyncio.run(asyncio.sleep(0))
    assert stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob").json()["code"] == "run_in_progress"
    assert stack.call("PATCH", f"/pipelines/{pipeline['id']}", json={"name": "x"}).json()["code"] == "run_in_progress"
    # Two requests that pass the check together still get one run.
    assert stack.services.store.create_run(pipeline=pipeline["id"], team=TEAM, environment="development",
                                           revision=1, triggered_by="bob") is None
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    # Waiting for a run that does not finish gives the run as it is, not an error.
    waited = stack.call("GET", f"/runs/{run['id']}?wait=1")
    assert waited.status_code == 200 and waited.json()["status"] == "running"
    assert stack.call("GET", f"/runs/{run['id']}?wait=601").json()["code"] == "invalid_request"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").json() == {"cancelling": True, "run": run["id"]}
    # wait on get returns as soon as the run is final.
    assert stack.call("GET", f"/runs/{run['id']}?wait=30").json()["status"] == "cancelled"
    finished = stack.call("POST", f"/runs/{run['id']}/cancel", "bob")
    assert finished.status_code == 409 and finished.json()["code"] == "run_finished"


def test_changing_where_a_pipeline_connects_removes_its_secrets(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}"
    stack.call("POST", f"{path}/runs", "bob", json={"wait": 30})
    old_name = stack.call("PATCH", path, json={"name": "crm api", "expected_revision": 2})
    assert old_name.status_code == 422 and old_name.json()["code"] == "invalid_request"
    renamed = stack.call("PATCH", path, json={"name": "crm api", "expectedRevision": 2}).json()
    assert renamed["name"] == "crm api" and renamed["secretsSet"] == ["token"] and "hint" not in renamed
    assert renamed["revision"] == 3 and renamed["description"] == "" and renamed["namespace"] == "raw_crm"
    stale = stack.call("PATCH", path, json={"description": "x", "expectedRevision": 2})
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    # A move that fails keeps dlt's state.
    stale_move = stack.call("PATCH", path, json={"namespace": "raw_crm_v2", "expectedRevision": 2}).json()
    assert stale_move["code"] == "revision_conflict" and stack.services.store.state(pipeline["id"]) is not None
    moved = stack.call("PATCH", path, json={"source": {**REST, "client": {**REST["client"],
                                                                           "base_url": "https://evil.example.org/"}}})
    assert moved.json()["secretsMissing"] == ["token"] and "secrets were removed" in moved.json()["hint"]
    assert stack.call("PATCH", path, "bob", json={"name": "y"}).json()["code"] == "forbidden_role"
    elsewhere = stack.call("PATCH", path, json={"namespace": "raw_crm_v2"}).json()
    assert "from scratch" in elsewhere["hint"] and stack.services.store.state(pipeline["id"]) is None


def test_delete_needs_the_name_and_keeps_tables(stack):
    pipeline = ready(stack)
    stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30})
    path = f"/pipelines/{pipeline['id']}"
    # The old route and its JSON body are gone.
    assert stack.call("POST", f"{path}/delete", json={"confirm_name": "crm"}).status_code == 404
    missing = stack.call("DELETE", path)
    assert missing.status_code == 422 and missing.json()["code"] == "invalid_request"
    wrong = stack.call("DELETE", path, params={"confirmName": "nope"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirmation_mismatch"
    assert stack.call("DELETE", path, "bob", params={"confirmName": "crm"}).json()["code"] == "forbidden_role"
    assert stack.call("GET", path).json()["name"] == "crm"
    deleted = stack.call("DELETE", path, params={"confirmName": "crm"})
    assert deleted.status_code == 200 and set(deleted.json()) == {"deleted", "pipeline", "note"}
    assert deleted.json()["deleted"] is True and deleted.json()["pipeline"] == pipeline["id"]
    assert "stay" in deleted.json()["note"]
    gone = stack.call("GET", path)
    assert gone.status_code == 404 and gone.json()["code"] == "unknown_pipeline"


def test_other_teams_see_nothing(stack):
    pipeline = ready(stack)
    run = stack.call("POST", f"/pipelines/{pipeline['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.call("GET", "/pipelines", "dave").json() == {"pipelines": []}
    listed = stack.call("GET", "/pipelines", "carol").json()
    assert list(listed) == ["pipelines"] and [p["id"] for p in listed["pipelines"]] == [pipeline["id"]]
    assert listed["pipelines"][0]["lastRun"]["id"] == run["id"] and listed["pipelines"][0]["role"] == "reader"
    assert stack.call("GET", f"/pipelines?team={OTHER}", "carol").json() == {"pipelines": []}
    assert stack.call("GET", f"/pipelines/{pipeline['id']}", "dave").json()["code"] == "unknown_pipeline"
    assert stack.call("GET", f"/runs/{run['id']}", "dave").json()["code"] == "unknown_run"
    assert stack.call("GET", f"/runs/{run['id']}/logs", "dave").json()["code"] == "unknown_run"


def test_schedules_never_overlap(stack):
    pipeline = ready(stack)
    path = f"/pipelines/{pipeline['id']}/schedule"
    assert stack.call("PUT", path, json={"cron": "* * * * *"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, json={"cron": "not cron"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, "bob", json={"cron": "0 5 * * *"}).json()["code"] == "forbidden_role"
    # Not enabled, a schedule has no next run.
    paused = stack.call("PUT", path, json={"cron": "0 5 * * *", "enabled": False}).json()
    assert paused["enabled"] is False and paused["nextRunAt"] is None
    schedule = stack.call("PUT", path, json={"cron": "0 5 * * *"}).json()
    assert set(schedule) == {"cron", "enabled", "nextRunAt", "lastRun", "createdBy", "createdAt"}
    assert schedule["cron"] == "0 5 * * *" and schedule["enabled"] is True and schedule["lastRun"] is None
    assert schedule["createdBy"] == "alice" and seconds(schedule["nextRunAt"]) > seconds(schedule["createdAt"])
    assert stack.call("GET", f"/pipelines/{pipeline['id']}").json()["schedule"] == schedule
    scheduler_tick = __import__("ingestion.scheduler", fromlist=["Scheduler"]).Scheduler(stack.services).tick
    stack.launcher.hold = True
    started = stack.client.portal.call(scheduler_tick, seconds(schedule["nextRunAt"]) + 1)
    assert len(started) == 1
    again = stack.client.portal.call(scheduler_tick, seconds(schedule["nextRunAt"]) + 86400 + 1)
    assert again == []
    # The store keeps the cron text; the response says that a schedule started the run.
    assert stack.services.store.run(started[0])["schedule"] == "0 5 * * *"
    run = stack.call("GET", f"/runs/{started[0]}").json()
    assert run["scheduled"] is True and run["triggeredBy"] == "schedule" and run["agent"] is None
    assert "schedule" not in run
    assert stack.call("GET", f"/pipelines/{pipeline['id']}").json()["schedule"]["lastRun"] == started[0]
    assert stack.call("DELETE", path).json() == {"deleted": True, "pipeline": pipeline["id"]}
    missing = stack.call("DELETE", path)
    assert missing.status_code == 404 and missing.json()["code"] == "no_schedule"


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


def test_the_gateway_masks_encoded_secrets_in_run_logs_and_errors():
    from ingestion.secrets import Secrets
    line = ("HTTPError: 401 Client Error for url: https://api.example.test/x?zz=Ab12%2Bcd%2Fef%3Dgh "
            "'Ab12+cd/ef=gh' Basic dXNlcjpBYjEyK2Nk")
    text = Secrets("ab" * 32).scrub(line, ["Ab12+cd/ef=gh"])
    assert "Ab12" not in text and "dXNlcjpB" not in text and "for url: https://api.example.test/x?" in text


def test_every_error_has_the_same_body(stack, monkeypatch):
    def refused(response, status, code):
        body = response.json()
        assert response.status_code == status and body["code"] == code, response.text
        assert isinstance(body["error"], str) and body["error"] and set(body) <= {"error", "code", "detail"}

    refused(stack.call("GET", "/nothing-here"), 404, "not_found")
    refused(stack.client.get("/nothing/here"), 404, "not_found")
    refused(stack.call("PUT", "/me"), 405, "method_not_allowed")
    refused(stack.call("GET", "/me", "mallory"), 401, "unauthorized")
    refused(stack.call("GET", "/pipelines?environment=test"), 422, "invalid_request")
    refused(stack.call("GET", "/pipelines/pl-" + "0" * 24), 404, "unknown_pipeline")
    refused(stack.call("GET", "/runs/run-" + "0" * 24), 404, "unknown_run")
    refused(stack.call("POST", "/pipelines/validate", json={"sourceType": "rest_api", "source": {"x": 1}}), 422,
            "invalid_pipeline")

    async def broken(caller):
        raise RuntimeError("boom")
    monkeypatch.setattr(stack.services, "whoami", broken)
    refused(stack.call("GET", "/me"), 500, "internal_error")
    assert IngestionError(418, "x").code == "internal_error" and IngestionError(405, "x").code == "method_not_allowed"


def test_tools_return_what_the_api_returns(stack):
    """An MCP tool gives the REST operation's body, unchanged and without a wrapper of its own."""
    pipeline = ready(stack)
    id = pipeline["id"]
    run = stack.tool("run_pipeline", "bob", pipeline=id, wait=30, full_refresh=True)
    assert run["status"] == "success" and run["fullRefresh"] and run["results"] == [{"name": "customers", "rows": 3}]
    schedule = stack.tool("set_schedule", pipeline=id, cron="0 5 * * *")
    assert schedule == stack.call("GET", f"/pipelines/{id}").json()["schedule"]
    for tool, arguments, path in (
            ("whoami", {}, "/me"),
            ("list_environments", {"team": TEAM}, f"/teams/{TEAM}/environments"),
            ("list_templates", {}, "/templates"),
            ("list_pipelines", {}, "/pipelines"),
            ("list_pipelines", {"team": TEAM, "environment": "production"},
             f"/pipelines?team={TEAM}&environment=production"),
            ("get_pipeline", {"pipeline": id}, f"/pipelines/{id}"),
            ("list_runs", {"pipeline": id, "limit": 1}, f"/pipelines/{id}/runs?limit=1"),
            ("get_run", {"run": run["id"]}, f"/runs/{run['id']}"),
            ("get_run", {"run": run["id"], "wait": 5}, f"/runs/{run['id']}?wait=5"),
            ("get_run_logs", {"run": run["id"], "limit": 1}, f"/runs/{run['id']}/logs?limit=1")):
        assert stack.tool(tool, "carol", **arguments) == stack.call("GET", path, "carol").json(), tool
    assert list(stack.tool("list_runs", pipeline=id)) == ["runs"]
    assert stack.tool("get_run", run=run["id"]) == run
    assert stack.tool("get_run_logs", run=run["id"])["lines"] == ["Loaded 3 rows"]
    changed = stack.tool("update_pipeline", pipeline=id, description="CRM customers", expected_revision=2)
    assert changed["description"] == "CRM customers" and changed["revision"] == 3
    with pytest.raises(ToolRefused, match=r"\[revision_conflict\]"):
        stack.tool("update_pipeline", pipeline=id, description="again", expected_revision=2)
    assert stack.tool("set_pipeline_secrets", pipeline=id, secrets={"token": None})["secretsMissing"] == ["token"]
    with pytest.raises(ToolRefused, match=r"\[secrets_missing\]"):
        stack.tool("run_pipeline", "bob", pipeline=id)
    with pytest.raises(ToolRefused, match=r"\[run_finished\]"):
        stack.tool("cancel_run", "bob", run=run["id"])
    assert stack.tool("delete_schedule", pipeline=id) == {"deleted": True, "pipeline": id}
    with pytest.raises(ToolRefused, match=r"\[no_schedule\]"):
        stack.tool("delete_schedule", pipeline=id)
    with pytest.raises(ToolRefused, match=r"\[confirmation_mismatch\]"):
        stack.tool("delete_pipeline", pipeline=id, confirm_name="nope")
    deleted = stack.tool("delete_pipeline", pipeline=id, confirm_name="crm")
    assert deleted["deleted"] is True and deleted["pipeline"] == id and "id" not in deleted
    environment = stack.tool("disable_environment", team=TEAM, environment="development")
    assert environment["enabled"] is False and environment["enabledBy"] is None and environment["team"] == TEAM
    with pytest.raises(ToolRefused, match=r"\[environment_not_enabled\]"):
        stack.tool("disable_environment", team=TEAM, environment="development")
    assert stack.tool("enable_environment", team=TEAM, environment="development") == {
        **environment, "enabled": True, "enabledBy": "alice"}


def test_tool_parameters_and_annotations(stack):
    tools = stack.tools()

    def parameters(tool):
        return tools[tool]["inputSchema"]["properties"]

    def hints(tool):
        a = tools[tool]["annotations"]
        return a["readOnlyHint"], a["destructiveHint"], a["idempotentHint"], a["openWorldHint"]

    for tool in ("run_pipeline", "get_run"):
        wait = parameters(tool)["wait"]
        assert (wait["default"], wait["minimum"], wait["maximum"]) == (0, 0, 600), tool
    limit = parameters("get_run_logs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (500, 1, 5000)
    assert "tail" not in parameters("get_run_logs")
    limit = parameters("list_runs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 200)
    assert {"pipeline", "confirm_name"} == set(parameters("delete_pipeline"))
    assert {"expected_revision", "source_type"} <= set(parameters("update_pipeline"))
    assert "full_refresh" in parameters("run_pipeline")
    # A resource is named by its noun, never "id".
    assert not [name for name in tools if "id" in parameters(name)]
    read = (True, False, True, False)
    for tool in ("whoami", "list_environments", "list_templates", "list_pipelines", "get_pipeline",
                 "validate_pipeline", "list_runs", "get_run", "get_run_logs"):
        assert hints(tool) == read, tool
    # These can remove secrets and load state, or overwrite a value nobody can read back.
    for tool in ("update_pipeline", "set_pipeline_secrets", "delete_pipeline", "delete_schedule",
                 "disable_environment"):
        assert hints(tool) == (False, True, False, False), tool
    for tool in ("enable_environment", "create_pipeline", "cancel_run", "set_schedule"):
        assert hints(tool) == (False, False, False, False), tool
    # A run connects to the source, outside the platform.
    assert hints("run_pipeline") == (False, False, False, True)


def test_the_contract_describes_errors_and_the_shared_shapes(stack):
    schema = stack.app.openapi()
    schemas = schema["components"]["schemas"]
    assert {"Error", "Me", "Environment", "EnvironmentList", "Run", "RunList", "RunLogs", "CancelResult",
            "Schedule", "Deleted", "TemplateList", "PipelineList"} <= set(schemas)
    assert "HTTPValidationError" not in schemas
    assert set(schemas["Error"]["properties"]) == {"error", "code", "detail"}
    assert schemas["Error"]["required"] == ["error", "code"]
    operations = {op["operationId"]: (method, path, op) for path, item in schema["paths"].items()
                  for method, op in item.items()}
    for id, (_, _, op) in operations.items():
        for status in ("401", "403", "404", "409", "422", "500", "502", "503"):
            assert op["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/Error"), id

    def answer(id, status="200"):
        return operations[id][2]["responses"][status]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]

    assert answer("whoami") == "Me" and answer("listEnvironments") == "EnvironmentList"
    assert answer("enableEnvironment") == answer("disableEnvironment") == "Environment"
    assert answer("listTemplates") == "TemplateList" and answer("listPipelines") == "PipelineList"
    assert answer("runPipeline", "202") == answer("getRun") == "Run" and answer("listRuns") == "RunList"
    assert answer("getRunLogs") == "RunLogs" and answer("cancelRun") == "CancelResult"
    assert answer("setSchedule") == "Schedule" and answer("createPipeline", "201") == "Pipeline"
    assert answer("deletePipeline") == answer("deleteSchedule") == "Deleted"
    assert "results" in schemas["Run"]["properties"] and "results" not in schemas["RunItem"]["properties"]
    assert operations["deletePipeline"][:2] == ("delete", "/api/v1/pipelines/{pipeline}")
    assert operations["setPipelineSecrets"][:2] == ("patch", "/api/v1/pipelines/{pipeline}/secrets")

    def query(id):
        return {p["name"]: p for p in operations[id][2]["parameters"] if p["in"] == "query"}

    assert query("deletePipeline")["confirmName"]["required"] is True
    assert query("getRun")["wait"]["schema"] == {"type": "integer", "maximum": 600, "minimum": 0, "default": 0,
                                                 "title": "Wait", "description": query("getRun")["wait"]["description"]}
    assert set(query("getRunLogs")) == {"limit"} and query("getRunLogs")["limit"]["schema"]["maximum"] == 5000
    assert set(schemas["RunInput"]["properties"]) == {"fullRefresh", "wait"}
    assert "expectedRevision" in schemas["PipelineChange"]["properties"]
    # No path parameter is called id.
    assert not [path for path in schema["paths"] if "{id}" in path]


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
    assert in_range("1.0.0", supported) and in_range("1.4.2", supported)
    assert not in_range("0.1.4", supported) and not in_range("2.0.0", supported)


def test_other_team_cannot_create_in_a_foreign_team(stack):
    assert stack.create("dave").json()["code"] == "forbidden_role"
    assert stack.call("POST", "/pipelines", "dave", json={
        "team": OTHER, "name": "x", "database": "sales", "namespace": "raw", "sourceType": "rest_api",
        "source": REST}).json()["code"] == "invalid_database"


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import asyncio

    import httpx
    import pytest

    from ingestion.auth import BrowserLogin
    from ingestion.errors import IngestionError
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(IngestionError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(IngestionError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/runs"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_ingestion_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"
