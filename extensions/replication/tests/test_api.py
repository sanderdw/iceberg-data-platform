import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from replication import templates, validate
from replication.api import OPERATIONS
from replication.bridge_client import in_range
from replication.config import ROOT, contract_range
from replication.errors import ReplicationError
from tests.conftest import DATABASES, OTHER, SOURCE, TEAM, make_stack

SECRET = "sk-live-very-secret-value"


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def ready(stack, **fields):
    """An enabled environment and a replication with its secret set."""
    enable(stack)
    replication = stack.create(**fields).json()
    assert stack.call("PUT", f"/replications/{replication['id']}/secrets",
                      json={"secrets": {"password": SECRET}}).status_code == 200
    return replication


def test_whoami_and_environments(stack):
    me = stack.call("GET", "/me").json()
    assert me["extension"] == "replication" and me["sourceTypes"] == ["postgres"]
    assert stack.call("POST", f"/teams/{TEAM}/environments/development", "carol").json()["code"] == "forbidden_role"
    enable(stack)
    envs = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert envs[0]["enabled"] and {d["name"] for d in envs[0]["databases"]} == {"sales", "raw-data"}
    assert stack.call("GET", f"/teams/{TEAM}/environments", "dave").json()["code"] == "not_a_member"


def test_only_administrators_create_replications(stack):
    for user in ("bob", "carol", "dave"):
        assert stack.create(user).json()["code"] == "forbidden_role"
    created = stack.create()
    assert created.status_code == 201
    body = created.json()
    assert body["database"] == DATABASES[("sales", "development")] and body["databaseName"] == "sales"
    assert body["secretsMissing"] == ["password"] and body["secretsSet"] == []
    assert "set_replication_secrets" in body["hint"] and "check_source" in body["hint"]
    assert body["tables"]["public.orders"] == {"mode": "cdc", "table": "orders", "partition": "/{created_at,month}",
                                               "appendOnly": False}
    assert body["options"] == {"deleteMode": "pos", "firstRun": "copy"}
    assert "CREATE PUBLICATION \"iceberg_crm\"" in body["setupSql"] and "olake_reader" in body["setupSql"]
    # A partitioned table's deletes only land in the right partition with the full row.
    assert 'ALTER TABLE "public"."orders" REPLICA IDENTITY FULL;' in body["setupSql"]
    assert "customers" not in body["setupSql"].split("GRANT")[0] and len(body["warnings"]) == 1
    assert stack.create().json()["code"] == "name_taken"
    assert stack.create(name="x", database="db-" + "2" * 32).json()["code"] == "unknown_database"
    # Tables can be chosen after the source check.
    empty = stack.create(name="later", tables=None).json()
    assert empty["tables"] == {} and "Choose tables" in empty["hint"]


@pytest.mark.parametrize(("change", "field"), [
    ({"password": "plain"}, "source.password"),
    ({"host": "10.1.2.3"}, "source.host"),
    ({"host": "127.0.0.1"}, "source.host"),
    ({"host": "169.254.169.254"}, "source.host"),
    ({"host": "localhost"}, "source.host"),
    ({"host": "db example"}, "source.host"),
    ({"tls": "prefer"}, "source.tls"),
    ({"ssh_config": {"host": "jump"}}, "source"),
    ({"port": 70000}, "source.port"),
    ({"slot": "Bad-Slot"}, "source.slot"),
    ({"database": "crm; drop"}, "source.database"),
])
def test_sources_are_checked(stack, change, field):
    response = stack.call("POST", "/replications/validate", json={"sourceType": "postgres",
                                                                  "source": {**SOURCE, **change}})
    assert response.status_code == 422 and response.json()["detail"]["field"] == field, response.json()


@pytest.mark.parametrize(("tables", "options", "field"), [
    ({"orders": {}}, None, None),
    ({"public.orders": {"mode": "append"}}, None, "tables.public.orders"),
    ({"public.orders": {"mode": "incremental"}}, None, "tables.public.orders.cursor"),
    ({"public.orders": {"mode": "cdc", "cursor": "x"}}, None, "tables.public.orders"),
    ({"public.orders": {"partition": "month(created_at)"}}, None, "tables.public.orders.partition"),
    ({"public.orders": {}, "sales.orders": {}}, None, "tables.sales.orders"),
    ({"public.orders": {}}, {"deleteMode": "eq"}, "options.deleteMode"),
    ({"public.orders": {}}, {"firstRun": "now"}, "options.firstRun"),
])
def test_tables_and_options_are_checked(stack, tables, options, field):
    response = stack.call("POST", "/replications/validate", json={"sourceType": "postgres", "source": SOURCE,
                                                                  "tables": tables, "options": options})
    assert response.status_code == 422, response.json()
    if field:
        assert response.json()["detail"]["field"] == field, response.json()


def test_change_data_capture_needs_a_slot_and_publication(stack):
    source = {k: v for k, v in SOURCE.items() if k not in ("slot", "publication")}
    cdc = stack.call("POST", "/replications/validate", json={"sourceType": "postgres", "source": source,
                                                             "tables": {"public.orders": {"mode": "cdc"}}})
    assert cdc.json()["detail"]["field"] == "source.slot"
    incremental = stack.call("POST", "/replications/validate", json={
        "sourceType": "postgres", "source": source,
        "tables": {"public.orders": {"mode": "incremental", "cursor": "updated_at", "table": "orders_v2"}}}).json()
    assert incremental["valid"] and incremental["tables"]["public.orders"]["table"] == "orders_v2"


def test_private_sources_need_an_allowed_network(tmp_path, monkeypatch):
    def checker(client):
        return lambda host: client.post(
            "/api/v1/replications/validate", headers={"Authorization": "Bearer alice"},
            json={"sourceType": "postgres", "source": {**SOURCE, "host": host}}).json()

    _, _, _, app = make_stack(tmp_path / "a", monkeypatch, allowed_networks=validate.networks("10.0.0.0/8"))
    with TestClient(app) as client:
        check = checker(client)
        assert check("10.1.2.3")["valid"]
        assert check("192.168.1.1")["detail"]["field"] == "source.host"
        # Loopback and link-local stay refused, whatever the networks.
        assert check("127.0.0.1")["detail"]["field"] == "source.host"
    _, _, _, app = make_stack(tmp_path / "b", monkeypatch, allow_private=True)
    with TestClient(app) as client:
        assert checker(client)("host.docker.internal")["valid"]


def test_every_template_is_a_valid_replication():
    for template in templates.TEMPLATES:
        checked = validate.definition(template["sourceType"], template["source"], template["tables"],
                                      template["options"])
        assert checked["secrets"] == template["secrets"], template["id"]
        assert "$secret" not in templates.setup_sql(template["sourceType"], template["source"], template["tables"])


def test_secrets_are_write_only_and_encrypted(stack):
    replication = stack.create().json()
    path = f"/replications/{replication['id']}/secrets"
    assert stack.call("PUT", path, "bob", json={"secrets": {"password": SECRET}}).json()["code"] == "forbidden_role"
    assert stack.call("PUT", path, json={"secrets": {"other": SECRET}}).json()["code"] == "invalid_replication"
    # The log scrubbers do not mask values this short.
    assert stack.call("PUT", path, json={"secrets": {"password": "abc"}}).json()["code"] == "invalid_replication"
    response = stack.call("PUT", path, json={"secrets": {"password": SECRET}})
    assert response.json()["secretsSet"] == ["password"] and response.json()["secretsMissing"] == []
    for text in (response.text, stack.call("GET", f"/replications/{replication['id']}").text,
                 stack.call("GET", "/replications").text):
        assert SECRET not in text
    database = sqlite3.connect(stack.settings.state_dir / "state.sqlite3")
    raw = database.execute("SELECT secrets FROM replications").fetchone()
    assert SECRET not in raw[0] and "password" in json.loads(raw[0])
    removed = stack.call("PUT", path, json={"secrets": {"password": None}}).json()
    assert removed["secretsMissing"] == ["password"]


def test_runs_execute_as_the_team_with_state_between_runs(stack):
    replication = stack.create().json()
    path = f"/replications/{replication['id']}/runs"
    assert stack.call("POST", path, "bob").json()["code"] == "secrets_missing"
    stack.call("PUT", f"/replications/{replication['id']}/secrets", json={"secrets": {"password": SECRET}})
    assert stack.call("POST", path, "bob").json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.call("POST", path, "carol").json()["code"] == "forbidden_role"
    first = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert first["status"] == "success" and first["triggeredBy"] == "bob" and first["kind"] == "sync"
    tables = {t["name"]: t for t in first["summary"]["tables"]}
    assert tables["public.customers"] == {"name": "public.customers", "table": "customers", "records": 3,
                                          "replaced": 1, "deleteFiles": 1}
    assert first["summary"]["recordsWritten"] == 6 and first["summary"]["readUpTo"] == "0/1"
    started = stack.launcher.started[first["id"]]
    assert started["token"] == "token-write" and started["secrets"] == {"password": SECRET}
    assert stack.bridge.tokens[-1][1] == "write" and started["state"] is None
    # The gateway resolved the source and pins the address in the run.
    job = started["job"]
    assert job["warehouse"] == DATABASES[("sales", "development")] and job["namespace"] == "raw_crm"
    assert job["url"] == f"http://localhost:3014/#/replications/{replication['id']}"
    assert job["source"]["password"] == "$secret:password" and SECRET not in json.dumps(job)
    assert job["catalogUri"] == "http://polaris-control-plane:8181/api/catalog"
    assert job["publicStorageEndpoint"] == "http://localhost:9000" and job["storageEndpoint"] == "http://rustfs:9000"
    assert job["deadline"] == started["timeout"] - 180
    second = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[second["id"]]["state"]["runs"] == 1
    third = stack.call("POST", path, "bob", json={"wait": 30, "full_refresh": True}).json()
    assert stack.launcher.started[third["id"]]["state"] is None and third["fullRefresh"]
    runs = stack.call("GET", f"{path}?limit=2").json()
    assert [r["id"] for r in runs] == [third["id"], second["id"]]
    assert stack.call("GET", f"/replications/{replication['id']}").json()["lastRun"]["id"] == third["id"]


def test_the_source_address_is_pinned_and_checked_before_each_run(stack, monkeypatch):
    replication = ready(stack)
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.launcher.hosts[run["id"]] == {"db.example.com": "93.184.216.34"}
    # A name that now resolves to a private address is refused at run time.
    stack.services.store.update_replication(replication["id"], None,
                                            source={**SOURCE, "host": "intranet.example.com"})
    refused = stack.call("POST", f"/replications/{replication['id']}/runs", "bob").json()
    assert refused["code"] == "source_refused"


def test_demo_mode_lets_the_run_resolve_the_source(tmp_path, monkeypatch):
    # host.docker.internal resolves only inside the run, where the launcher maps it to the Docker host.
    monkeypatch.setattr("replication.validate.resolve_addresses", lambda host: (_ for _ in ()).throw(OSError()))
    assert validate.pin("host.docker.internal", allow_private=True) is None
    with pytest.raises(ReplicationError) as refused:
        validate.pin("host.docker.internal")
    assert refused.value.code == "source_unresolved"


def test_a_partial_first_copy_keeps_its_state(stack):
    replication = ready(stack)
    path = f"/replications/{replication['id']}/runs"
    stack.launcher.status = "partial"
    first = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert first["status"] == "partial" and first["summary"]["firstCopyComplete"] is False
    stack.launcher.status = "success"
    second = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[second["id"]]["state"]["runs"] == 1


def test_a_source_check_lists_checks_and_tables(stack):
    replication = ready(stack, tables=None)
    assert stack.call("POST", f"/replications/{replication['id']}/runs", "bob").json()["code"] == "no_tables"
    assert stack.call("POST", f"/replications/{replication['id']}/check", "carol").json()["code"] == "forbidden_role"
    check = stack.call("POST", f"/replications/{replication['id']}/check", "bob").json()
    assert check["connected"] and not check["ready"] and check["status"] == "success"
    assert [c["name"] for c in check["checks"] if not c["passed"]] == ["publication"]
    assert check["tables"][0]["name"] == "public.orders" and check["tables"][0]["primaryKey"] == ["id"]
    started = next(s for s in stack.launcher.started.values() if s["job"]["kind"] == "check")
    assert started["state"] is None and started["timeout"] == 600
    shown = stack.call("GET", f"/replications/{replication['id']}", "carol").json()
    assert shown["lastCheck"]["checks"] == check["checks"] and shown["lastRun"] is None
    # A check is not a sync: it never replaces the saved state.
    assert stack.services.store.state(replication["id"]) is None


def test_logs_mask_secrets(stack):
    replication = ready(stack)
    stack.launcher.log = f"failed to connect with password {SECRET}\nAuthorization: Bearer abcdefghijk\n"
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    lines = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()["lines"]
    assert SECRET not in "\n".join(lines) and "abcdefghijk" not in "\n".join(lines) and len(lines) == 2


def test_one_run_per_replication_and_cancel(stack):
    replication = ready(stack)
    stack.launcher.hold = True
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob").json()
    for _ in range(100):
        status = stack.call("GET", f"/runs/{run['id']}").json()["status"]
        if status == "running" and run["id"] in stack.launcher.started:
            break
        asyncio.run(asyncio.sleep(0))
    assert stack.call("POST", f"/replications/{replication['id']}/runs", "bob").json()["code"] == "run_in_progress"
    changed = stack.call("PATCH", f"/replications/{replication['id']}", json={"name": "x"}).json()
    assert changed["code"] == "run_in_progress"
    # Two requests that pass the check together still get one run.
    assert stack.services.store.create_run(replication=replication["id"], team=TEAM, environment="development",
                                           revision=1, triggered_by="bob") is None
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").json()["cancelling"]
    done = stack.services.runs.wait(run["id"], 10)
    assert stack.client.portal.call(lambda: done)["status"] == "cancelled"


def test_changing_where_a_replication_connects_removes_its_secrets(stack):
    replication = ready(stack)
    path = f"/replications/{replication['id']}"
    stack.call("POST", f"{path}/runs", "bob", json={"wait": 30})
    renamed = stack.call("PATCH", path, json={"name": "crm orders", "expected_revision": 2}).json()
    assert renamed["name"] == "crm orders" and renamed["secretsSet"] == ["password"] and "hint" not in renamed
    stale = stack.call("PATCH", path, json={"description": "x", "expected_revision": 2}).json()
    assert stale["code"] == "revision_conflict"
    # A move that fails keeps OLake's state.
    stale_move = stack.call("PATCH", path, json={"namespace": "raw_crm_v2", "expected_revision": 2}).json()
    assert stale_move["code"] == "revision_conflict" and stack.services.store.state(replication["id"]) is not None
    tables = stack.call("PATCH", path, json={"tables": {"public.orders": {"mode": "cdc"}}}).json()
    assert list(tables["tables"]) == ["public.orders"] and "hint" not in tables
    assert stack.services.store.state(replication["id"]) is not None
    moved = stack.call("PATCH", path, json={"source": {**SOURCE, "host": "evil.example.org"}})
    assert moved.json()["secretsMissing"] == ["password"] and "secrets were removed" in moved.json()["hint"]
    assert "copies everything again" in moved.json()["hint"] and stack.services.store.state(replication["id"]) is None
    assert stack.call("PATCH", path, "bob", json={"name": "y"}).json()["code"] == "forbidden_role"


def test_delete_needs_the_name_and_keeps_tables(stack):
    replication = ready(stack)
    stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30})
    path = f"/replications/{replication['id']}/delete"
    assert stack.call("POST", path, json={"confirm_name": "nope"}).json()["code"] == "confirmation_mismatch"
    assert stack.call("POST", path, "bob", json={"confirm_name": "crm"}).json()["code"] == "forbidden_role"
    deleted = stack.call("POST", path, json={"confirm_name": "crm"}).json()
    assert deleted["deleted"] and "stay" in deleted["note"]
    assert deleted["dropSlotSql"] == "SELECT pg_drop_replication_slot('iceberg_crm');" and "disk" in deleted["warning"]
    assert stack.call("GET", f"/replications/{replication['id']}").json()["code"] == "unknown_replication"


def test_other_teams_see_nothing(stack):
    replication = ready(stack)
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.call("GET", "/replications", "dave").json() == []
    assert stack.call("GET", f"/replications/{replication['id']}", "dave").json()["code"] == "unknown_replication"
    assert stack.call("GET", f"/runs/{run['id']}", "dave").json()["code"] == "unknown_run"
    assert stack.call("GET", f"/runs/{run['id']}/logs", "dave").json()["code"] == "unknown_run"


def test_schedules_never_overlap(stack):
    replication = ready(stack)
    path = f"/replications/{replication['id']}/schedule"
    assert stack.call("PUT", path, json={"cron": "* * * * *"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, json={"cron": "*/5 * * * *"}).json()["cron"] == "*/5 * * * *"
    assert stack.call("PUT", path, json={"cron": "not cron"}).json()["code"] == "invalid_cron"
    assert stack.call("PUT", path, "bob", json={"cron": "0 5 * * *"}).json()["code"] == "forbidden_role"
    schedule = stack.call("PUT", path, json={"cron": "0 5 * * *"}).json()
    assert schedule["cron"] == "0 5 * * *" and schedule["enabled"]
    scheduler_tick = __import__("replication.scheduler", fromlist=["Scheduler"]).Scheduler(stack.services).tick
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
    replication = ready(stack)
    stack.bridge.token_lifetime = 3600
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.launcher.started[run["id"]]["timeout"] == 2700
    stack.bridge.token_lifetime = 1000
    short = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    assert short["status"] == "error" and "POLARIS_TOKEN_LIFETIME" in short["error"]


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.post("/api/v1/replications/validate", json={})
    assert response.json()["code"] == "csrf"


def test_secrets_need_a_key(tmp_path):
    from replication.secrets import Secrets
    with pytest.raises(ReplicationError) as exc:
        Secrets("").seal("token", "x")
    assert exc.value.code == "secrets_unavailable"
    sealed = Secrets("ab" * 32).seal("token", "value")
    assert Secrets("ab" * 32).open("token", sealed) == "value"
    with pytest.raises(ReplicationError):
        Secrets("cd" * 32).open("token", sealed)
    with pytest.raises(ReplicationError):
        Secrets("ab" * 32).open("password", sealed)


def test_the_gateway_masks_encoded_secrets_in_run_logs_and_errors():
    from replication.secrets import Secrets
    line = ("HTTPError: 401 Client Error for url: https://api.example.test/x?zz=Ab12%2Bcd%2Fef%3Dgh "
            "'Ab12+cd/ef=gh' Basic dXNlcjpBYjEyK2Nk")
    text = Secrets("ab" * 32).scrub(line, ["Ab12+cd/ef=gh"])
    assert "Ab12" not in text and "dXNlcjpB" not in text and "for url: https://api.example.test/x?" in text


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = stack.client.portal.call(_tools, stack.app)
    assert set(OPERATIONS.values()) == mcp
    contract = ROOT / "contracts" / "replication-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/replication/contracts/replication-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


async def _tools(app):
    from replication.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name for tool in await mcp.list_tools()}


def test_supports_the_platforms_bridge_contract():
    supported = contract_range()
    assert in_range("0.1.0", supported) and in_range("0.1.4", supported)
    assert not in_range("0.2.0", supported) and not in_range("1.0.0", supported)


def test_other_team_cannot_create_in_a_foreign_team(stack):
    assert stack.create("dave").json()["code"] == "forbidden_role"
    assert stack.call("POST", "/replications", "dave", json={
        "team": OTHER, "name": "x", "database": "sales", "namespace": "raw", "sourceType": "postgres",
        "source": SOURCE}).json()["code"] == "unknown_database"


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import httpx

    from replication.auth import BrowserLogin
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/replications")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(ReplicationError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(ReplicationError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/replications")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/replications"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_replication_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"


def test_ca_certificates_are_checked(stack):
    pem = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n-----END CERTIFICATE-----"
    def check(**change):
        return stack.call("POST", "/replications/validate", json={"sourceType": "postgres",
                                                                  "source": {**SOURCE, **change}}).json()
    assert check(caCertificate=pem)["source"]["caCertificate"] == pem
    assert check(caCertificate="not a certificate")["detail"]["field"] == "source.caCertificate"
    assert check(caCertificate="-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----")["detail"]["field"] \
        == "source.caCertificate"
    assert check(caCertificate=pem, tls="disable")["detail"]["field"] == "source.caCertificate"


def test_an_append_only_table_needs_replica_identity_full_everywhere():
    tables = {"public.orders": {"mode": "cdc"}, "public.audit_log": {"mode": "cdc"}}
    # Keyed tables only: unchanged large values are read back, so no table needs it.
    assert "REPLICA IDENTITY FULL;" not in templates.setup_sql("postgres", SOURCE, tables)
    assert templates.warnings(tables) == []
    # One keyless table (from the last source check) turns that off for the whole replication.
    sql = templates.setup_sql("postgres", SOURCE, tables, keyless={"public.audit_log"})
    assert 'ALTER TABLE "public"."orders" REPLICA IDENTITY FULL;' in sql
    assert 'ALTER TABLE "public"."audit_log" REPLICA IDENTITY FULL;' in sql
    assert "__olake_unavailable_value__" in templates.warnings(tables, {"public.audit_log"})[0]


def test_the_setup_sql_grants_every_schema():
    sql = templates.setup_sql("postgres", SOURCE, {"crm.orders": {"mode": "cdc"}, "public.customers": {"mode": "cdc"}})
    assert 'GRANT USAGE ON SCHEMA "crm" TO "olake_reader";' in sql
    assert 'GRANT USAGE ON SCHEMA "public" TO "olake_reader";' in sql
    assert sql.index("GRANT USAGE") < sql.index("GRANT SELECT")
