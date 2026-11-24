import json
import time

import pytest

from maintenance import policy
from maintenance.api import OPERATIONS
from maintenance.bridge_client import in_range
from maintenance.config import ROOT, contract_range
from maintenance.errors import MaintenanceError
from maintenance.runs import summarize
from maintenance.scheduler import Scheduler
from tests.conftest import DATABASES, DEV, TABLES, TEAM

PROD = DATABASES[("grid-capacity", "production")]


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def save(stack, user="alice", database=DEV, **body):
    return stack.call("PUT", f"/databases/{database}/policy", user, json=body)


def test_whoami_lists_the_defaults_and_where_it_is_enabled(stack):
    me = stack.call("GET", "/me").json()
    assert me["extension"] == "maintenance" and me["defaults"] == policy.DEFAULTS
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
    assert stack.call("POST", f"/teams/{TEAM}/environments/development", "bob").json()["code"] == "forbidden_role"
    enable(stack)
    assert stack.call("GET", "/me", "carol").json()["enabledEnvironments"][0]["environments"] == ["development"]
    assert stack.call("GET", f"/teams/{TEAM}/environments", "dave").json()["code"] == "not_a_member"


def test_runs_need_an_enabled_environment_and_a_writer(stack):
    assert stack.run().json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.run(user="carol").json()["code"] == "forbidden_role"
    assert stack.run(user="dave").json()["code"] == "unknown_database"


def test_a_health_check_uses_a_read_token_and_stores_each_tables_health(stack):
    enable(stack)
    run = stack.run().json()
    assert run["status"] == "success" and run["mode"] == "inspect", run
    assert stack.bridge.tokens[-1][1] == "read"
    job = stack.launcher.started[run["id"]]["job"]
    assert job["kind"] == "inspect" and job["warehouse"] == DEV and job["storageEndpoint"] == "http://rustfs:9000"
    assert job["policy"] == policy.DEFAULTS and job["deadline"] > time.time()
    tables = stack.call("GET", f"/tables?database={DEV}", "carol").json()
    assert {t["name"] for t in tables} == set(TABLES)
    readings = next(t for t in tables if t["name"] == TABLES[0])
    # Without a policy the defaults judge the table, and say there is none.
    assert readings["state"] == "no-policy" and "30 files to compact" in readings["reasons"]
    assert stack.call("GET", "/tables?attention=true", "carol").json() == []
    database = next(d for d in stack.call("GET", "/databases").json() if d["id"] == DEV)
    assert database["tables"] == 3 and not database["policy"] and database["lastRun"]["id"] == run["id"]


def test_policies_are_for_administrators_and_checked(stack):
    assert save(stack, "bob", settings={}).json()["code"] == "forbidden_role"
    assert stack.call("GET", f"/databases/{DEV}/policy", "carol").json()["exists"] is False
    bad = [({"compaction": {"targetFileSizeMb": 4}}, "between 16 and 512"),
           ({"orphanFiles": {"olderThanHours": 2}}, "between 24"),
           ({"expireSnapshots": {"retainLast": 0}}, "between 1"),
           ({"expireSnapshots": {"enabled": "yes"}}, "true or false"),
           ({"vacuum": {}}, "Unknown operation"),
           ({"compaction": {"targetFileSizeMb": 512, "maxInputMbPerRun": 256}}, "at least the target")]
    for settings, message in bad:
        response = save(stack, settings=settings)
        assert response.status_code == 422 and message in response.json()["error"], (settings, response.json())
    created = save(stack, settings={"orphanFiles": {"enabled": True}}, exclude=["raw.transformer_register"],
                   cron="0 2 * * *", expected_revision=0).json()
    assert created["exists"] and created["revision"] == 1 and created["settings"]["orphanFiles"]["enabled"]
    assert created["settings"]["expireSnapshots"] == policy.DEFAULTS["expireSnapshots"]
    assert created["schedule"]["enabled"] and created["schedule"]["nextRunAt"] > time.time()
    # A partial change keeps the rest; a stale revision is refused.
    changed = save(stack, settings={"compaction": {"targetFileSizeMb": 256}}, expected_revision=1).json()
    assert changed["revision"] == 2 and changed["settings"]["orphanFiles"]["enabled"]
    assert changed["exclude"] == ["raw.transformer_register"] and changed["schedule"]["cron"] == "0 2 * * *"
    assert save(stack, settings={}, expected_revision=1).json()["code"] == "revision_conflict"
    assert save(stack, cron="*/10 * * * *").json()["code"] == "invalid_cron"
    assert save(stack, cron="").json()["schedule"] is None
    assert stack.call("DELETE", f"/databases/{DEV}/policy", "bob").json()["code"] == "forbidden_role"
    assert stack.call("DELETE", f"/databases/{DEV}/policy").json()["deleted"]
    assert stack.call("DELETE", f"/databases/{DEV}/policy").json()["code"] == "no_policy"


def test_maintenance_needs_a_policy_and_a_write_token(stack):
    enable(stack)
    assert stack.run("maintain").json()["code"] == "no_policy"
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
    assert table["state"] == "healthy" and table["lastMaintainedAt"] and table["lastActions"][0]["status"] == "done"
    assert stack.call("GET", f"/databases/{DEV}/tables/raw.transformer_register").json()["state"] == "excluded"
    logs = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()
    assert logs["lines"] == ["Maintenance runner 0.0.1: maintain"]


def test_one_run_per_database_and_writers_stop_runs(stack):
    enable(stack)
    stack.launcher.hold = True
    first = stack.call("POST", f"/databases/{DEV}/runs", "bob", json={"mode": "inspect"}).json()
    assert stack.run().json()["code"] == "run_in_progress"
    assert stack.call("POST", f"/runs/{first['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    for _ in range(100):
        if stack.call("GET", f"/runs/{first['id']}").json()["status"] == "running":
            break
        time.sleep(0.01)
    assert stack.call("POST", f"/runs/{first['id']}/cancel", "bob").json()["cancelling"]
    for _ in range(200):
        status = stack.call("GET", f"/runs/{first['id']}").json()["status"]
        if status not in ("queued", "running"):
            break
        time.sleep(0.01)
    assert status == "cancelled"


def test_a_failed_table_makes_a_partial_run(stack):
    enable(stack)
    stack.launcher.fail_table = TABLES[1]
    run = stack.run("dry-run").json()
    assert run["status"] == "partial"
    failed = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[1]}").json()
    assert failed["state"] == "failed" and failed["lastError"] == "commit conflict"


def test_a_table_filter_keeps_other_tables(stack):
    enable(stack)
    stack.run()
    stack.launcher.tables = [TABLES[0]]
    stack.run(tables=[TABLES[0]])
    assert len(stack.call("GET", f"/tables?database={DEV}").json()) == 3
    # A complete health check forgets tables that are gone.
    stack.run()
    assert [t["name"] for t in stack.call("GET", f"/tables?database={DEV}").json()] == [TABLES[0]]


def test_schedules_maintain_and_skip_busy_databases(stack):
    enable(stack)
    save(stack, cron="0 2 * * *")
    scheduler = Scheduler(stack.services, interval=0)
    started = stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400)
    assert len(started) == 1
    run = stack.services.store.run(started[0])
    assert run["kind"] == "maintain" and run["triggered_by"] == "schedule" and run["schedule"] == "0 2 * * *"
    # The next time is moved forward even when nothing starts.
    assert stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400) == []
    paused = save(stack, scheduleEnabled=False).json()
    assert paused["schedule"] == {"cron": "0 2 * * *", "enabled": False, "nextRunAt": None}


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
    response = stack.client.put(f"/api/v1/databases/{DEV}/policy", json={})
    assert response.status_code == 403 and response.json()["code"] == "csrf"


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = stack.client.portal.call(_tools, stack.app)
    assert set(OPERATIONS.values()) == mcp
    contract = ROOT / "contracts" / "maintenance-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/maintenance/contracts/maintenance-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


async def _tools(app):
    from maintenance.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name for tool in await mcp.list_tools()}


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
