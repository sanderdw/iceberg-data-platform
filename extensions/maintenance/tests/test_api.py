import json
import re
import time
from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from maintenance import policy
from maintenance.api import OPERATIONS
from maintenance.bridge_client import in_range
from maintenance.config import ROOT, contract_range
from maintenance.errors import MaintenanceError
from maintenance.runs import summarize
from maintenance.scheduler import Scheduler
from maintenance.services import timestamp
from tests.conftest import DATABASES, DEV, OTHER, TABLES, TEAM

PROD = DATABASES[("grid-capacity", "production")]
RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
RUN_KEYS = {"id", "database", "team", "environment", "mode", "tables", "status", "triggeredBy", "agent",
            "scheduled", "createdAt", "startedAt", "finishedAt", "exitCode", "summary", "error"}
STATUSES = {"queued", "running", "success", "incomplete", "partial", "failed", "error", "cancelled"}


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def save(stack, user="alice", database=DEV, **body):
    return stack.call("PATCH", f"/databases/{database}/policy", user, json=body)


def schedule(stack, user="alice", database=DEV, **body):
    return stack.call("PUT", f"/databases/{database}/schedule", user, json=body)


def seconds(value):
    assert RFC3339.match(value), value
    return datetime.fromisoformat(value).timestamp()


def refused(stack, code, tool, user="alice", **arguments):
    with pytest.raises(ToolError) as error:
        stack.tool(tool, user, **arguments)
    assert f"[{code}]" in str(error.value), error.value


def test_times_are_rfc_3339_in_utc_with_milliseconds():
    assert timestamp(1795511702.1234) == "2026-11-24T09:15:02.123Z"
    assert timestamp(0) == "1970-01-01T00:00:00.000Z" and timestamp(None) is None


def test_whoami_lists_the_defaults_and_where_it_is_enabled(stack):
    me = stack.call("GET", "/me").json()
    # Built field by field: the Bridge's own keys, such as sharedDatabases, do not pass through.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "operations", "defaults"}
    assert me["user"] == {"id": "portal-alice" + "0" * 27, "name": "alice"}
    assert me["memberships"] == [{"team": TEAM, "teamName": "grid-planning", "role": "admin"}]
    assert me["databases"][0] == {"id": DEV, "name": "grid-capacity", "team": TEAM, "environment": "development",
                                  "status": "ready"}
    assert me["extension"] == "maintenance" and me["defaults"] == policy.DEFAULTS
    assert me["operations"] == policy.LABELS and me["environments"] == ["development", "acceptance", "production"]
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
    assert stack.call("POST", f"/teams/{TEAM}/environments/development", "bob").json()["code"] == "forbidden_role"
    enable(stack)
    assert stack.call("GET", "/me", "carol").json()["enabledEnvironments"][0]["environments"] == ["development"]
    assert stack.tool("whoami") == stack.call("GET", "/me").json()


def test_environments_are_one_entry_shape_in_every_operation(stack):
    refusal = stack.call("GET", f"/teams/{TEAM}/environments", "dave")
    assert refusal.status_code == 403 and refusal.json()["code"] == "not_a_member"
    databases = [{"id": DEV, "name": "grid-capacity"}, {"id": DATABASES[("grid-assets", "development")],
                                                        "name": "grid-assets"}]
    off = {"team": TEAM, "environment": "development", "enabled": False, "enabledBy": None, "databases": databases}
    on = {**off, "enabled": True, "enabledBy": "alice"}
    listed = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert list(listed) == ["environments"] and listed["environments"][0] == off
    assert [e["environment"] for e in listed["environments"]] == ["development", "acceptance", "production"]
    enabled = stack.call("POST", f"/teams/{TEAM}/environments/development")
    # The environment entry, not the Bridge's automation principal.
    assert enabled.status_code == 200 and enabled.json() == on
    assert stack.call("GET", f"/teams/{TEAM}/environments").json()["environments"][0] == on
    assert stack.tool("list_environments", team=TEAM) == stack.call("GET", f"/teams/{TEAM}/environments").json()
    disabled = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert disabled.status_code == 200 and disabled.json() == off
    again = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert again.status_code == 409 and again.json()["code"] == "environment_not_enabled"
    assert stack.tool("enable_environment", team=TEAM, environment="production")["enabled"] is True
    assert stack.tool("disable_environment", team=TEAM, environment="production") == {
        "team": TEAM, "environment": "production", "enabled": False, "enabledBy": None,
        "databases": [{"id": PROD, "name": "grid-capacity"}]}
    refused(stack, "environment_not_enabled", "disable_environment", team=TEAM, environment="production")


def test_runs_need_an_enabled_environment_and_a_writer(stack):
    refusal = stack.run()
    assert refusal.status_code == 409 and refusal.json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.run(user="carol").json()["code"] == "forbidden_role"
    unknown = stack.run(user="dave")
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_database"


def test_a_health_check_uses_a_read_token_and_stores_each_tables_health(stack):
    enable(stack)
    run = stack.run().json()
    assert run["status"] == "success" and run["mode"] == "inspect", run
    assert stack.bridge.tokens[-1][1] == "read"
    job = stack.launcher.started[run["id"]]["job"]
    assert job["kind"] == "inspect" and job["warehouse"] == DEV and job["storageEndpoint"] == "http://rustfs:9000"
    assert job["policy"] == policy.DEFAULTS and job["deadline"] > time.time()
    listed = stack.call("GET", f"/tables?database={DEV}", "carol").json()
    assert set(listed) == {"tables", "total"} and listed["total"] == 3
    tables = listed["tables"]
    assert {t["name"] for t in tables} == set(TABLES)
    readings = next(t for t in tables if t["name"] == TABLES[0])
    assert set(readings) == {"database", "databaseName", "team", "environment", "name", "state", "reasons", "health",
                             "inspectedAt", "inspectedBy", "lastRun", "lastStatus", "lastError", "lastActions",
                             "lastMaintainedAt"}
    # Without a policy the defaults judge the table, and say there is none.
    assert readings["state"] == "no-policy" and "30 files to compact" in readings["reasons"]
    assert readings["lastRun"] == run["id"] and readings["lastError"] is None
    assert readings["lastMaintainedAt"] is None and time.time() - seconds(readings["inspectedAt"]) < 60
    assert stack.call("GET", "/tables?attention=true", "carol").json() == {"tables": [], "total": 0}
    listed = stack.call("GET", "/databases").json()
    assert list(listed) == ["databases"]
    database = next(d for d in listed["databases"] if d["id"] == DEV)
    assert set(database) == {"id", "name", "team", "teamName", "environment", "role", "policy", "schedule", "tables",
                             "attention", "inspected", "lastRun"}
    assert database["tables"] == 3 and not database["policy"] and database["schedule"] is None
    # A run in a list carries no per-table results.
    assert database["lastRun"]["id"] == run["id"] and set(database["lastRun"]) == RUN_KEYS
    assert stack.tool("list_databases", "carol") == stack.call("GET", "/databases", "carol").json()
    assert stack.tool("list_table_health", "carol", database=DEV) == stack.call(
        "GET", f"/tables?database={DEV}", "carol").json()


def test_table_health_is_limited_and_counts_what_matches(stack):
    enable(stack)
    stack.run()
    assert stack.call("GET", "/tables").json()["total"] == 3
    first = stack.call("GET", "/tables?limit=2").json()
    # total counts before the limit.
    assert len(first["tables"]) == 2 and first["total"] == 3
    assert stack.tool("list_table_health", limit=1) == {"tables": first["tables"][:1], "total": 3}
    for limit in (0, 5001):
        response = stack.call("GET", f"/tables?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    assert len(stack.call("GET", "/tables?limit=5000").json()["tables"]) == 3
    with pytest.raises(ToolError):
        stack.tool("list_table_health", limit=5001)
    # A filter that names a team you are not in is refused; one that names an unknown database is not found.
    other = stack.call("GET", f"/tables?team={OTHER}")
    assert other.status_code == 403 and other.json()["code"] == "not_a_member"
    assert stack.call("GET", f"/databases?team={OTHER}").json()["code"] == "not_a_member"
    unknown = stack.call("GET", "/tables?database=db-" + "9" * 32)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_database"
    one = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[0]}", "carol").json()
    assert one["role"] == "reader" and one["policy"]["exists"] is False and set(one["recentRuns"][0]) == RUN_KEYS
    assert stack.tool("get_table_health", "carol", database=DEV, table=TABLES[0]) == one
    missing = stack.call("GET", f"/databases/{DEV}/tables/raw.nothing")
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_table"


def test_policies_are_for_administrators_and_checked(stack):
    assert save(stack, "bob", settings={}).json()["code"] == "forbidden_role"
    none = stack.call("GET", f"/databases/{DEV}/policy", "carol").json()
    assert none["exists"] is False and none["revision"] == 0 and none["schedule"] is None
    assert none["settings"] == policy.DEFAULTS and none["createdAt"] is None and none["updatedBy"] is None
    bad = [({"compaction": {"targetFileSizeMb": 4}}, "between 16 and 512"),
           ({"orphanFiles": {"olderThanHours": 2}}, "between 24"),
           ({"expireSnapshots": {"retainLast": 0}}, "between 1"),
           ({"expireSnapshots": {"enabled": "yes"}}, "true or false"),
           ({"vacuum": {}}, "Unknown operation"),
           ({"compaction": {"targetFileSizeMb": 512, "maxInputMbPerRun": 256}}, "at least the target")]
    for settings, message in bad:
        response = save(stack, settings=settings)
        assert response.status_code == 422 and message in response.json()["error"], (settings, response.json())
        assert response.json()["code"] == "invalid_policy"
    # A change is a PATCH. The old method and the old field names are gone.
    replaced = stack.call("PUT", f"/databases/{DEV}/policy", json={})
    assert replaced.status_code == 405 and replaced.json()["code"] == "method_not_allowed"
    for old in ({"expected_revision": 0}, {"cron": "0 2 * * *"}, {"scheduleEnabled": True}):
        response = save(stack, **old)
        assert response.status_code == 422 and response.json()["code"] == "invalid_request", old
    assert save(stack, settings={}, expectedRevision=3).json()["code"] == "revision_conflict"
    response = save(stack, settings={"orphanFiles": {"enabled": True}}, exclude=["raw.transformer_register"],
                    expectedRevision=0)
    created = response.json()
    assert response.status_code == 200 and created["exists"] and created["revision"] == 1
    assert created["settings"]["orphanFiles"]["enabled"]
    assert created["settings"]["expireSnapshots"] == policy.DEFAULTS["expireSnapshots"]
    assert created["schedule"] is None and created["createdBy"] == created["updatedBy"] == "alice"
    assert time.time() - seconds(created["createdAt"]) < 60 and seconds(created["updatedAt"])
    assert set(created) == set(none)
    # A partial change keeps the rest; a stale revision is refused.
    changed = save(stack, settings={"compaction": {"targetFileSizeMb": 256}}, expectedRevision=1).json()
    assert changed["revision"] == 2 and changed["settings"]["orphanFiles"]["enabled"]
    assert changed["exclude"] == ["raw.transformer_register"]
    stale = save(stack, settings={}, expectedRevision=1)
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    assert stack.tool("get_policy", "carol", database=DEV) == stack.call(
        "GET", f"/databases/{DEV}/policy", "carol").json()
    assert stack.call("DELETE", f"/databases/{DEV}/policy", "bob").json()["code"] == "forbidden_role"
    deleted = stack.call("DELETE", f"/databases/{DEV}/policy")
    assert deleted.status_code == 200 and deleted.json() == {
        "deleted": True, "database": DEV, "note": "Scheduled maintenance stops. Table health and past runs stay."}
    missing = stack.call("DELETE", f"/databases/{DEV}/policy")
    assert missing.status_code == 404 and missing.json()["code"] == "no_policy"


def test_the_policy_tool_changes_only_what_it_names(stack):
    refused(stack, "forbidden_role", "update_policy", "bob", database=DEV)
    # Without a policy it creates one from the defaults.
    created = stack.tool("update_policy", database=DEV, expected_revision=0)
    assert created["exists"] and created["revision"] == 1 and created["settings"] == policy.DEFAULTS
    refused(stack, "revision_conflict", "update_policy", database=DEV, expected_revision=0)
    changed = stack.tool("update_policy", database=DEV, settings={"rewriteManifests": {"minManifests": 50}},
                         exclude=[TABLES[2]], expected_revision=1)
    assert changed["settings"]["rewriteManifests"] == {"enabled": True, "minManifests": 50}
    assert changed["exclude"] == [TABLES[2]] and changed == stack.call("GET", f"/databases/{DEV}/policy").json()
    assert stack.tool("update_policy", database=DEV, settings={"orphanFiles": {"enabled": True}})["exclude"] == [
        TABLES[2]]
    assert stack.tool("delete_policy", database=DEV)["deleted"] is True
    refused(stack, "no_policy", "delete_policy", database=DEV)


def test_a_schedule_is_set_and_removed_on_its_own(stack):
    # A schedule applies the policy, so it needs one.
    early = schedule(stack, cron="0 2 * * *")
    assert early.status_code == 409 and early.json()["code"] == "policy_required"
    missing = stack.call("DELETE", f"/databases/{DEV}/schedule")
    assert missing.status_code == 404 and missing.json()["code"] == "no_schedule"
    save(stack, exclude=[TABLES[2]])
    assert schedule(stack, "bob", cron="0 2 * * *").json()["code"] == "forbidden_role"
    response = schedule(stack, cron="0 2 * * *")
    made = response.json()
    assert response.status_code == 200 and set(made) == {"cron", "enabled", "nextRunAt", "lastRun"}
    assert made["cron"] == "0 2 * * *" and made["enabled"] is True and made["lastRun"] is None
    assert seconds(made["nextRunAt"]) > time.time()
    # Maintenance keeps its own minimum of an hour, stricter than the platform's five minutes.
    for cron in ("*/10 * * * *", "*/30 * * * *", "0 2 * *", "not a cron"):
        response = schedule(stack, cron=cron)
        assert response.status_code == 422 and response.json()["code"] == "invalid_cron", cron
    assert schedule(stack, cron="0 * * * *").json()["cron"] == "0 * * * *"
    assert schedule(stack, cron="").json()["code"] == "invalid_request"
    assert schedule(stack, enabled=False).json()["code"] == "invalid_request"
    # Paused: the schedule stays, and has no next run.
    assert schedule(stack, cron="0 2 * * *", enabled=False).json() == {
        "cron": "0 2 * * *", "enabled": False, "nextRunAt": None, "lastRun": None}
    # It is read with the policy and the database, and is not part of the policy's revision.
    read = stack.call("GET", f"/databases/{DEV}/policy").json()
    assert read["schedule"]["cron"] == "0 2 * * *" and read["revision"] == 1
    kept = save(stack, settings={"compaction": {"minInputFiles": 3}}, expectedRevision=1).json()
    assert kept["revision"] == 2 and kept["schedule"]["cron"] == "0 2 * * *" and kept["exclude"] == [TABLES[2]]
    listed = next(d for d in stack.call("GET", "/databases").json()["databases"] if d["id"] == DEV)
    assert listed["schedule"] == kept["schedule"]
    assert stack.call("DELETE", f"/databases/{DEV}/schedule", "bob").json()["code"] == "forbidden_role"
    removed = stack.call("DELETE", f"/databases/{DEV}/schedule")
    assert removed.status_code == 200 and removed.json() == {"deleted": True, "database": DEV}
    after = stack.call("GET", f"/databases/{DEV}/policy").json()
    assert after["schedule"] is None and after["exists"] and after["revision"] == 2
    assert stack.call("DELETE", f"/databases/{DEV}/schedule").json()["code"] == "no_schedule"


def test_the_schedule_tools_do_the_same(stack):
    refused(stack, "policy_required", "set_schedule", database=DEV, cron="0 2 * * *")
    refused(stack, "no_schedule", "delete_schedule", database=DEV)
    stack.tool("update_policy", database=DEV)
    refused(stack, "forbidden_role", "set_schedule", "bob", database=DEV, cron="0 2 * * *")
    refused(stack, "invalid_cron", "set_schedule", database=DEV, cron="*/5 * * * *")
    made = stack.tool("set_schedule", database=DEV, cron="0 3 * * 0")
    assert made["enabled"] is True and made == stack.call("GET", f"/databases/{DEV}/policy").json()["schedule"]
    assert stack.tool("set_schedule", database=DEV, cron="0 3 * * 0", enabled=False)["nextRunAt"] is None
    assert stack.tool("delete_schedule", database=DEV) == {"deleted": True, "database": DEV}
    refused(stack, "no_schedule", "delete_schedule", database=DEV)


def test_maintenance_needs_a_policy_and_a_write_token(stack):
    enable(stack)
    early = stack.run("maintain")
    # Not the delete's no_policy: that one means there is nothing to remove.
    assert early.status_code == 409 and early.json()["code"] == "policy_required"
    dry = stack.run("dry-run").json()
    assert dry["status"] == "success" and stack.bridge.tokens[-1][1] == "read"
    assert stack.launcher.started[dry["id"]]["job"]["dryRun"] is True
    save(stack, exclude=["raw.transformer_register"])
    run = stack.run("maintain").json()
    assert run["status"] == "success" and stack.bridge.tokens[-1][1] == "write"
    job = stack.launcher.started[run["id"]]["job"]
    assert job["kind"] == "maintain" and not job["dryRun"] and job["exclude"] == ["raw.transformer_register"]
    detail = stack.call("GET", f"/runs/{run['id']}", "carol").json()
    assert {r["name"]: r["status"] for r in detail["results"]}["raw.transformer_register"] == "skipped"
    table = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[0]}", "carol").json()
    assert table["state"] == "healthy" and table["lastActions"][0]["status"] == "done"
    assert time.time() - seconds(table["lastMaintainedAt"]) < 60
    assert stack.call("GET", f"/databases/{DEV}/tables/raw.transformer_register").json()["state"] == "excluded"


def test_a_run_has_one_shape_when_started_read_and_listed(stack):
    enable(stack)
    response = stack.run()
    started = response.json()
    # 202 with the run as get returns it, results included, also when wait saw it finish.
    assert response.status_code == 202 and set(started) == RUN_KEYS | {"databaseName", "role", "results"}
    assert started["status"] == "success" and started["mode"] == "inspect" and started["tables"] == []
    assert started["triggeredBy"] == "bob" and started["agent"] == "ext-maintenance-mcp"
    assert started["scheduled"] is False and "schedule" not in started
    assert started["databaseName"] == "grid-capacity" and started["role"] == "writer"
    assert started["exitCode"] == 0 and started["error"] is None
    assert seconds(started["createdAt"]) <= seconds(started["startedAt"]) <= seconds(started["finishedAt"])
    assert [r["name"] for r in started["results"]] == TABLES
    # The summary holds the totals; neither the tables nor the automation principal.
    assert started["summary"]["totals"]["tables"] == 3
    assert "tables" not in started["summary"] and "principal" not in started["summary"]
    read = stack.call("GET", f"/runs/{started['id']}", "bob").json()
    assert read == started
    assert stack.tool("get_run", "bob", run=started["id"]) == read
    listed = stack.call("GET", f"/databases/{DEV}/runs", "bob").json()
    assert list(listed) == ["runs"] and listed["runs"] == [{k: read[k] for k in RUN_KEYS}]
    assert stack.tool("list_runs", "bob", database=DEV) == listed
    # A body is optional, and so is waiting: the run is then returned as it is, with no results yet.
    assert stack.call("POST", f"/databases/{PROD}/runs", "bob").json()["code"] == "environment_not_enabled"
    stack.launcher.hold = True
    queued = stack.call("POST", f"/databases/{DEV}/runs", "bob")
    assert queued.status_code == 202 and queued.json()["status"] in ("queued", "running")
    assert queued.json()["results"] == [] and queued.json()["finishedAt"] is None
    stack.call("POST", f"/runs/{queued.json()['id']}/cancel", "bob")
    assert len(stack.call("GET", f"/databases/{DEV}/runs?limit=1").json()["runs"]) == 1
    for limit in (0, 201):
        response = stack.call("GET", f"/databases/{DEV}/runs?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    with pytest.raises(ToolError):
        stack.tool("list_runs", database=DEV, limit=201)
    unknown = stack.call("GET", "/runs/run-" + "0" * 24)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_run"


def test_a_run_log_returns_its_last_lines(stack):
    enable(stack)
    run = stack.run("dry-run").json()
    logs = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()
    assert logs == {"run": run["id"], "status": "success", "lines": ["Maintenance runner 0.0.1: maintain"]}
    (stack.launcher.artifacts / run["id"] / "out" / "run.log").write_text("one\ntwo\nthree\n")
    assert stack.call("GET", f"/runs/{run['id']}/logs?limit=2").json()["lines"] == ["two", "three"]
    assert stack.tool("get_run_logs", run=run["id"], limit=1) == {"run": run["id"], "status": "success",
                                                                  "lines": ["three"]}
    assert len(stack.tool("get_run_logs", run=run["id"])["lines"]) == 3
    for limit in (0, 5001):
        response = stack.call("GET", f"/runs/{run['id']}/logs?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    # tail was the old name: it no longer limits anything.
    assert len(stack.call("GET", f"/runs/{run['id']}/logs?tail=1").json()["lines"]) == 3
    with pytest.raises(ToolError):
        stack.tool("get_run_logs", run=run["id"], limit=5001)


def test_one_run_per_database_and_writers_stop_runs(stack):
    enable(stack)
    stack.launcher.hold = True
    first = stack.call("POST", f"/databases/{DEV}/runs", "bob", json={"mode": "inspect"}).json()
    busy = stack.run()
    assert busy.status_code == 409 and busy.json()["code"] == "run_in_progress"
    assert stack.call("POST", f"/runs/{first['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    for _ in range(100):
        if stack.call("GET", f"/runs/{first['id']}").json()["status"] == "running":
            break
        time.sleep(0.01)
    # Waiting returns the run as it is when the time is up; it is not an error that it still runs.
    waited = stack.call("GET", f"/runs/{first['id']}?wait=1")
    assert waited.status_code == 200 and waited.json()["status"] == "running"
    stopping = stack.call("POST", f"/runs/{first['id']}/cancel", "bob")
    assert stopping.status_code == 200 and stopping.json() == {"cancelling": True, "run": first["id"]}
    # And it returns as soon as the run is final.
    began = time.monotonic()
    final = stack.call("GET", f"/runs/{first['id']}?wait=600").json()
    assert final["status"] == "cancelled" and time.monotonic() - began < 30
    assert stack.tool("get_run", run=first["id"], wait=600)["status"] == "cancelled"
    finished = stack.call("POST", f"/runs/{first['id']}/cancel", "bob")
    assert finished.status_code == 409 and finished.json()["code"] == "run_finished"
    refused(stack, "run_finished", "cancel_run", "bob", run=first["id"])
    for wait in (-1, 601):
        response = stack.call("GET", f"/runs/{first['id']}?wait={wait}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
        assert stack.call("POST", f"/databases/{DEV}/runs", "bob", json={"wait": wait}).status_code == 422
        with pytest.raises(ToolError):
            stack.tool("get_run", run=first["id"], wait=wait)


def test_a_failed_table_makes_a_partial_run(stack):
    enable(stack)
    stack.launcher.fail_table = TABLES[1]
    run = stack.run("dry-run").json()
    assert run["status"] == "partial" and run["error"] == "Some tables failed. The others were maintained."
    assert run["status"] in STATUSES and run["summary"]["totals"]["failed"] == 1
    failed = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[1]}").json()
    assert failed["state"] == "failed" and failed["lastError"] == "commit conflict"


def test_a_table_filter_keeps_other_tables(stack):
    enable(stack)
    stack.run()
    stack.launcher.tables = [TABLES[0]]
    assert stack.run(tables=[TABLES[0]]).json()["tables"] == [TABLES[0]]
    assert stack.call("GET", f"/tables?database={DEV}").json()["total"] == 3
    # A complete health check forgets tables that are gone.
    stack.run()
    assert [t["name"] for t in stack.call("GET", f"/tables?database={DEV}").json()["tables"]] == [TABLES[0]]


def test_schedules_maintain_and_skip_busy_databases(stack):
    enable(stack)
    save(stack)
    assert schedule(stack, cron="0 2 * * *").json()["lastRun"] is None
    scheduler = Scheduler(stack.services, interval=0)
    started = stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400)
    assert len(started) == 1
    stored = stack.services.store.run(started[0])
    assert stored["kind"] == "maintain" and stored["triggered_by"] == "schedule" and stored["schedule"] == "0 2 * * *"
    run = stack.call("GET", f"/runs/{started[0]}?wait=10").json()
    # What is stored as the cron text is shown as a yes or no.
    assert run["scheduled"] is True and run["triggeredBy"] == "schedule" and run["agent"] is None
    assert run["mode"] == "maintain" and "schedule" not in run
    # The next time is moved forward even when nothing starts.
    assert stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400) == []
    # The schedule names the last run it started, not a later one that a person started.
    by_hand = stack.run().json()
    assert by_hand["scheduled"] is False
    read = stack.call("GET", f"/databases/{DEV}/policy").json()["schedule"]
    assert read["lastRun"] == started[0] and seconds(read["nextRunAt"]) > time.time() + 2 * 86400
    listed = next(d for d in stack.call("GET", "/databases").json()["databases"] if d["id"] == DEV)
    assert listed["schedule"]["lastRun"] == started[0] and listed["lastRun"]["id"] == by_hand["id"]
    paused = schedule(stack, cron="0 2 * * *", enabled=False).json()
    assert paused == {"cron": "0 2 * * *", "enabled": False, "nextRunAt": None, "lastRun": started[0]}
    assert stack.client.portal.call(scheduler.tick, time.time() + 9 * 86400) == []


def test_agents_inspect_with_one_tool_and_maintain_with_another(stack):
    enable(stack)
    refused(stack, "forbidden_role", "inspect_database", "carol", database=DEV)
    inspected = stack.tool("inspect_database", "bob", database=DEV, wait=5)
    assert inspected["status"] == "success" and inspected["mode"] == "inspect" and len(inspected["results"]) == 3
    assert stack.bridge.tokens[-1][1] == "read"
    dry = stack.tool("inspect_database", "bob", database=DEV, mode="dry-run", tables=[TABLES[0]], wait=5)
    assert dry["mode"] == "dry-run" and dry["tables"] == [TABLES[0]] and stack.bridge.tokens[-1][1] == "read"
    assert dry["results"][0]["actions"][0]["status"] == "planned"
    # The inspect tool cannot maintain, and the maintain tool does nothing else.
    with pytest.raises(ToolError):
        stack.tool("inspect_database", "bob", database=DEV, mode="maintain", wait=5)
    refused(stack, "policy_required", "run_maintenance", "bob", database=DEV)
    stack.tool("update_policy", database=DEV)
    maintained = stack.tool("run_maintenance", "bob", database=DEV, wait=5)
    assert maintained["mode"] == "maintain" and maintained["status"] == "success"
    assert stack.bridge.tokens[-1][1] == "write"
    assert maintained == stack.call("GET", f"/runs/{maintained['id']}", "bob").json()
    # Without wait a tool returns at once, and get_run waits for the end.
    started = stack.tool("inspect_database", "bob", database=DEV)
    assert stack.tool("get_run", "bob", run=started["id"], wait=30)["status"] == "success"


def test_the_result_is_reduced_to_what_the_api_shows():
    summary = summarize({"status": "success", "totals": {"tables": 1, "bad": "x"}, "tables": [
        {"name": "a.b", "status": "success", "actions": [{"operation": "compaction", "status": "done", "detail": "x",
                                                         "metrics": {"inputFiles": 3, "nested": {"no": 1}}}]}]})
    assert summary["totals"] == {"tables": 1}
    assert summary["tables"][0]["actions"][0]["metrics"] == {"inputFiles": 3}
    assert summarize(None) == {}


def test_attention_reasons():
    from maintenance.services import attention

    settings = policy.defaults()
    assert attention({"compactableFiles": 4}, settings, False) == []
    assert attention({"compactableFiles": 5, "expirableSnapshots": 2, "positionDeleteFiles": 5, "manifests": 101},
                     settings, False) == ["5 files to compact", "2 snapshots to expire", "5 delete files",
                                          "101 manifests"]
    assert attention({"compactableFiles": 50}, settings, True) == []


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.patch(f"/api/v1/databases/{DEV}/policy", json={})
    assert response.status_code == 403 and response.json()["code"] == "csrf"


def test_every_error_has_the_same_body(stack, monkeypatch):
    unknown = stack.call("GET", "/nothing/here")
    assert unknown.status_code == 404 and unknown.json() == {"error": "Not found.", "code": "not_found"}
    wrong = stack.call("POST", "/me")
    assert wrong.status_code == 405 and wrong.json()["code"] == "method_not_allowed" and wrong.json()["error"]
    assert stack.client.get("/missing.js").json() == {"error": "Not found.", "code": "not_found"}
    invalid = stack.call("GET", "/databases/not-an-id/policy")
    assert invalid.status_code == 422 and set(invalid.json()) == {"error", "code"}
    assert invalid.json()["code"] == "invalid_request"
    assert stack.call("GET", "/me", "mallory").json()["code"] == "unauthorized"

    async def broken(caller):
        raise RuntimeError("boom")

    monkeypatch.setattr(stack.services, "whoami", broken)
    failed = stack.call("GET", "/me")
    assert failed.status_code == 500 and failed.json() == {"error": "Something went wrong.", "code": "internal_error"}
    assert MaintenanceError(418, "teapot").code == "internal_error" and MaintenanceError(405, "no").code == (
        "method_not_allowed")


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    tools = stack.client.portal.call(_tools, stack.app)
    # One operation may have two tools: starting a run is inspect_database and run_maintenance.
    expected = {name for value in OPERATIONS.values() for name in ((value,) if isinstance(value, str) else value)}
    assert expected == set(tools) and len(tools) == 18
    assert OPERATIONS["runMaintenance"] == ("inspect_database", "run_maintenance")
    contract = ROOT / "contracts" / "maintenance-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/maintenance/contracts/maintenance-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


def test_the_contract_names_the_operations_and_their_shapes(stack):
    schema = stack.app.openapi()
    paths = schema["paths"]
    found = {op["operationId"]: (method, path) for path, item in paths.items() for method, op in item.items()}
    assert found["listTableHealth"] == ("get", "/api/v1/tables")
    assert found["getTableHealth"] == ("get", "/api/v1/databases/{database}/tables/{table}")
    assert found["updatePolicy"] == ("patch", "/api/v1/databases/{database}/policy")
    assert found["setSchedule"] == ("put", "/api/v1/databases/{database}/schedule")
    assert found["deleteSchedule"] == ("delete", "/api/v1/databases/{database}/schedule")
    assert not {"listTables", "getTable", "setPolicy"} & set(found)
    models = {"whoami": "Me", "listEnvironments": "EnvironmentList", "enableEnvironment": "Environment",
              "disableEnvironment": "Environment", "listDatabases": "DatabaseList",
              "listTableHealth": "TableHealthList", "getPolicy": "Policy", "updatePolicy": "Policy",
              "setSchedule": "Schedule", "deleteSchedule": "Deleted", "runMaintenance": "Run", "listRuns": "RunList",
              "getRun": "Run", "getRunLogs": "RunLogs", "cancelRun": "CancelResult"}
    for operation, (method, path) in found.items():
        responses = paths[path][method]["responses"]
        success = "202" if operation == "runMaintenance" else "200"
        assert set(responses) == {success, "401", "403", "404", "409", "422", "500", "502", "503"}, operation
        for status in set(responses) - {success}:
            assert responses[status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/Error"}, (operation, status)
        if operation in models:
            assert responses[success]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{models[operation]}"}, operation
    schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and set(schemas["Error"]["required"]) == {"error", "code"}
    assert set(schemas["Schedule"]["properties"]) == {"cron", "enabled", "nextRunAt", "lastRun"}
    assert set(schemas["PolicyInput"]["properties"]) == {"settings", "exclude", "expectedRevision"}
    assert set(schemas["RunSummary"]["properties"]) == RUN_KEYS
    assert set(schemas["RunSummary"]["properties"]["status"]["enum"]) == STATUSES
    parameters = {p["name"]: p["schema"] for p in paths["/api/v1/runs/{run}/logs"]["get"]["parameters"]}
    assert parameters["limit"] == {"type": "integer", "maximum": 5000, "minimum": 1, "default": 500,
                                   "title": "Limit"} and "tail" not in parameters
    wait = next(p for p in paths["/api/v1/runs/{run}"]["get"]["parameters"] if p["name"] == "wait")["schema"]
    assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 600, 0)
    limit = next(p for p in paths["/api/v1/tables"]["get"]["parameters"] if p["name"] == "limit")["schema"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 5000, 500)


def test_the_tools_say_what_they_take_and_what_they_change(stack):
    tools = stack.client.portal.call(_tools, stack.app)
    takes = {name: set(tool.input_schema["properties"]) for name, tool in tools.items()}
    assert not {"list_tables", "get_table", "set_policy"} & set(tools)
    assert takes["inspect_database"] == {"database", "mode", "tables", "wait"}
    assert tools["inspect_database"].input_schema["properties"]["mode"]["enum"] == ["inspect", "dry-run"]
    assert takes["run_maintenance"] == {"database", "tables", "wait"}
    assert takes["update_policy"] == {"database", "settings", "exclude", "expected_revision"}
    assert takes["set_schedule"] == {"database", "cron", "enabled"} and takes["delete_schedule"] == {"database"}
    assert takes["get_run"] == {"run", "wait"} and takes["get_run_logs"] == {"run", "limit"}
    assert takes["list_table_health"] == {"database", "team", "environment", "attention", "limit"}
    assert takes["get_table_health"] == {"database", "table"} and takes["list_runs"] == {"database", "limit"}
    for name in ("inspect_database", "run_maintenance", "get_run"):
        wait = tools[name].input_schema["properties"]["wait"]
        assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 600, 0), name
    hints = {name: (tool.annotations.read_only_hint, tool.annotations.destructive_hint,
                    tool.annotations.idempotent_hint, tool.annotations.open_world_hint)
             for name, tool in tools.items()}
    read, write, destructive = (True, False, True, False), (False, False, False, False), (False, True, False, False)
    # A health check or a dry run changes no table; only maintaining is destructive.
    assert hints["inspect_database"] == write and hints["run_maintenance"] == destructive
    assert {name for name, hint in hints.items() if hint == destructive} == {
        "run_maintenance", "delete_policy", "delete_schedule", "disable_environment"}
    assert {name for name, hint in hints.items() if hint == write} == {
        "inspect_database", "update_policy", "set_schedule", "enable_environment", "cancel_run"}
    assert {name for name, hint in hints.items() if hint == read} == {
        "whoami", "list_environments", "list_databases", "list_table_health", "get_table_health", "get_policy",
        "list_runs", "get_run", "get_run_logs"}


async def _tools(app):
    from maintenance.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name: tool for tool in await mcp.list_tools()}


def test_supports_the_platforms_bridge_contract():
    supported = contract_range()
    assert in_range("0.1.0", supported) and in_range("0.1.4", supported)
    assert not in_range("0.2.0", supported)


@pytest.mark.parametrize("value", [["a"], ["a.b", 3], ["x" * 400]])
def test_excluded_names_are_checked(value):
    with pytest.raises(MaintenanceError):
        policy.excluded(value)


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import asyncio

    import httpx
    import pytest

    from maintenance.auth import BrowserLogin
    from maintenance.errors import MaintenanceError
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(MaintenanceError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(MaintenanceError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/runs"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_maintenance_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"
