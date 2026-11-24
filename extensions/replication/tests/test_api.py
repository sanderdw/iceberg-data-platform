import asyncio
import json
import re
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from replication import templates, validate
from replication.api import OPERATIONS
from replication.bridge_client import in_range
from replication.config import ROOT, contract_range
from replication.errors import ReplicationError
from tests.conftest import DATABASES, OTHER, SOURCE, TEAM, ToolRefused, make_stack

SECRET = "sk-live-very-secret-value"
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
RUN_FIELDS = {"id", "replication", "environment", "status", "triggeredBy", "agent", "scheduled", "kind", "revision",
              "fullRefresh", "createdAt", "startedAt", "finishedAt", "exitCode", "summary", "error"}


def seconds(timestamp):
    assert TIMESTAMP.match(timestamp), timestamp
    return datetime.fromisoformat(timestamp).timestamp()


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def ready(stack, **fields):
    """An enabled environment and a replication with its secret set."""
    enable(stack)
    replication = stack.create(**fields).json()
    assert stack.call("PATCH", f"/replications/{replication['id']}/secrets",
                      json={"secrets": {"password": SECRET}}).status_code == 200
    return replication


def test_whoami_and_environments(stack):
    me = stack.call("GET", "/me").json()
    # Built field by field: nothing else the Bridge says about the caller (such as sharedDatabases) comes along.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "sourceTypes", "allowPrivateSources",
                       "allowedSourceNetworks"}
    assert me["user"] == {"id": "portal-alice" + "0" * 27, "name": "alice"}
    assert me["memberships"] == [{"team": TEAM, "teamName": TEAM[:9], "role": "admin"}]
    assert {"id": DATABASES[("sales", "development")], "name": "sales", "team": TEAM, "environment": "development",
            "status": "ready"} in me["databases"]
    assert me["extension"] == "replication" and me["sourceTypes"] == ["postgres"]
    assert me["allowPrivateSources"] is False and me["allowedSourceNetworks"] == []
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
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
    # A database named in the body that is not the team's in this environment: the field is wrong, not the path.
    elsewhere = stack.create(name="x", database="db-" + "2" * 32)
    assert elsewhere.status_code == 422 and elsewhere.json()["code"] == "invalid_database"
    assert TIMESTAMP.match(body["createdAt"]) and body["updatedAt"] == body["createdAt"]
    assert body["schedule"] is None and body["lastRun"] is None and body["lastCheck"] is None
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
    assert stack.call("PATCH", path, "bob", json={"secrets": {"password": SECRET}}).json()["code"] == "forbidden_role"
    assert stack.call("PATCH", path, json={"secrets": {"other": SECRET}}).json()["code"] == "invalid_replication"
    # The log scrubbers do not mask values this short.
    assert stack.call("PATCH", path, json={"secrets": {"password": "abc"}}).json()["code"] == "invalid_replication"
    # The call merges, so it is a PATCH; PUT is gone.
    assert stack.call("PUT", path, json={"secrets": {"password": SECRET}}).status_code == 405
    response = stack.call("PATCH", path, json={"secrets": {"password": SECRET}})
    assert response.json()["secretsSet"] == ["password"] and response.json()["secretsMissing"] == []
    for text in (response.text, stack.call("GET", f"/replications/{replication['id']}").text,
                 stack.call("GET", "/replications").text):
        assert SECRET not in text
    database = sqlite3.connect(stack.settings.state_dir / "state.sqlite3")
    raw = database.execute("SELECT secrets FROM replications").fetchone()
    assert SECRET not in raw[0] and "password" in json.loads(raw[0])
    removed = stack.call("PATCH", path, json={"secrets": {"password": None}}).json()
    assert removed["secretsMissing"] == ["password"]


def test_runs_execute_as_the_team_with_state_between_runs(stack):
    replication = stack.create().json()
    path = f"/replications/{replication['id']}/runs"
    assert stack.call("POST", path, "bob").json()["code"] == "secrets_missing"
    stack.call("PATCH", f"/replications/{replication['id']}/secrets", json={"secrets": {"password": SECRET}})
    not_enabled = stack.call("POST", path, "bob")
    assert not_enabled.status_code == 409 and not_enabled.json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.call("POST", path, "carol").json()["code"] == "forbidden_role"
    started = stack.call("POST", path, "bob", json={"wait": 30})
    assert started.status_code == 202
    first = started.json()
    assert set(first) == RUN_FIELDS | {"results"}
    assert first["status"] == "success" and first["triggeredBy"] == "bob" and first["kind"] == "sync"
    assert first["scheduled"] is False and first["agent"] == "ext-replication-mcp" and first["error"] is None
    # The tables are the run's results, beside its totals; the account it ran as is not shown.
    tables = {t["name"]: t for t in first["results"]}
    assert tables["public.customers"] == {"name": "public.customers", "table": "customers", "records": 3,
                                          "replaced": 1, "deleteFiles": 1}
    assert first["summary"]["recordsWritten"] == 6 and first["summary"]["readUpTo"] == "0/1"
    assert not {"tables", "principal", "status", "checks", "streams"} & set(first["summary"])
    assert first["summary"]["error"] is None
    assert seconds(first["createdAt"]) <= seconds(first["startedAt"]) <= seconds(first["finishedAt"])
    assert stack.call("GET", f"/runs/{first['id']}", "carol").json() == first
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
    detail = stack.call("GET", f"/replications/{replication['id']}").json()
    assert detail["lastRun"]["id"] == third["id"] and set(detail["lastRun"]) == RUN_FIELDS
    assert [r["id"] for r in detail["recentRuns"]] == [third["id"], second["id"], first["id"]]
    assert all(set(r) == RUN_FIELDS for r in detail["recentRuns"])


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


def test_an_incomplete_first_copy_keeps_its_state(stack):
    replication = ready(stack)
    path = f"/replications/{replication['id']}/runs"
    stack.launcher.status = "incomplete"
    first = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert first["status"] == "incomplete" and first["summary"]["firstCopyComplete"] is False
    assert first["error"] is None and first["exitCode"] == 0
    assert stack.call("GET", f"/replications/{replication['id']}").json()["lastRun"]["status"] == "incomplete"
    stack.launcher.status = "success"
    second = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[second["id"]]["state"]["runs"] == 1
    # "partial" was this status in earlier versions. A runner that still reports it is not accepted.
    stack.launcher.status = "partial"
    old = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert old["status"] == "failed"
    after = stack.call("POST", path, "bob", json={"wait": 30}).json()
    assert stack.launcher.started[after["id"]]["state"]["runs"] == 2


def test_a_source_check_lists_checks_and_tables(stack):
    replication = ready(stack, tables=None)
    assert stack.call("POST", f"/replications/{replication['id']}/runs", "bob").json()["code"] == "no_tables"
    assert stack.call("POST", f"/replications/{replication['id']}/check", "carol").json()["code"] == "forbidden_role"
    answer = stack.call("POST", f"/replications/{replication['id']}/check", "bob")
    check = answer.json()
    assert answer.status_code == 200
    assert set(check) == {"run", "status", "checkedAt", "connected", "ready", "message", "checks", "tables"}
    assert check["connected"] and not check["ready"] and check["status"] == "success"
    assert TIMESTAMP.match(check["checkedAt"])
    assert [c["name"] for c in check["checks"] if not c["passed"]] == ["publication"]
    assert check["tables"][0]["name"] == "public.orders" and check["tables"][0]["primaryKey"] == ["id"]
    started = next(s for s in stack.launcher.started.values() if s["job"]["kind"] == "check")
    assert started["state"] is None and started["timeout"] == 600
    shown = stack.call("GET", f"/replications/{replication['id']}", "carol").json()
    assert shown["lastCheck"] == check and shown["lastRun"] is None
    # The check is a run of its own kind: its findings are in lastCheck, not in the run's summary or results.
    run = stack.call("GET", f"/runs/{check['run']}", "carol").json()
    assert run["kind"] == "check" and run["results"] == [] and not {"checks", "streams"} & set(run["summary"])
    assert [r["kind"] for r in shown["recentRuns"]] == ["check"]
    # A check is not a sync: it never replaces the saved state.
    assert stack.services.store.state(replication["id"]) is None


def test_logs_mask_secrets(stack):
    replication = ready(stack)
    stack.launcher.log = f"failed to connect with password {SECRET}\nAuthorization: Bearer abcdefghijk\n"
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
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


def test_one_run_per_replication_and_cancel(stack):
    replication = ready(stack)
    stack.launcher.hold = True
    started = stack.call("POST", f"/replications/{replication['id']}/runs", "bob")
    run = started.json()
    # Without wait the answer is the run as it is now, in the same shape as a finished one.
    assert started.status_code == 202 and run["status"] == "queued" and run["results"] == []
    assert run["startedAt"] is None and run["finishedAt"] is None and run["exitCode"] is None
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
    # Waiting for a run that does not finish gives the run as it is, not an error.
    waited = stack.call("GET", f"/runs/{run['id']}?wait=1")
    assert waited.status_code == 200 and waited.json()["status"] == "running"
    assert stack.call("GET", f"/runs/{run['id']}?wait=601").json()["code"] == "invalid_request"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").json() == {"cancelling": True, "run": run["id"]}
    # wait on get returns as soon as the run is final.
    assert stack.call("GET", f"/runs/{run['id']}?wait=30").json()["status"] == "cancelled"
    finished = stack.call("POST", f"/runs/{run['id']}/cancel", "bob")
    assert finished.status_code == 409 and finished.json()["code"] == "run_finished"


def test_changing_where_a_replication_connects_removes_its_secrets(stack):
    replication = ready(stack)
    path = f"/replications/{replication['id']}"
    stack.call("POST", f"{path}/runs", "bob", json={"wait": 30})
    old_name = stack.call("PATCH", path, json={"name": "crm orders", "expected_revision": 2})
    assert old_name.status_code == 422 and old_name.json()["code"] == "invalid_request"
    renamed = stack.call("PATCH", path, json={"name": "crm orders", "expectedRevision": 2}).json()
    assert renamed["name"] == "crm orders" and renamed["secretsSet"] == ["password"] and "hint" not in renamed
    assert renamed["revision"] == 3 and renamed["description"] == "" and renamed["namespace"] == "raw_crm"
    stale = stack.call("PATCH", path, json={"description": "x", "expectedRevision": 2})
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    # A move that fails keeps OLake's state.
    stale_move = stack.call("PATCH", path, json={"namespace": "raw_crm_v2", "expectedRevision": 2}).json()
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
    path = f"/replications/{replication['id']}"
    # The old route and its JSON body are gone.
    assert stack.call("POST", f"{path}/delete", json={"confirm_name": "crm"}).status_code == 404
    missing = stack.call("DELETE", path)
    assert missing.status_code == 422 and missing.json()["code"] == "invalid_request"
    wrong = stack.call("DELETE", path, params={"confirmName": "nope"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirmation_mismatch"
    assert stack.call("DELETE", path, "bob", params={"confirmName": "crm"}).json()["code"] == "forbidden_role"
    assert stack.call("GET", path).json()["name"] == "crm"
    answer = stack.call("DELETE", path, params={"confirmName": "crm"})
    deleted = answer.json()
    assert answer.status_code == 200 and set(deleted) == {"deleted", "replication", "note", "dropSlotSql", "warning"}
    assert deleted["deleted"] is True and deleted["replication"] == replication["id"] and "stay" in deleted["note"]
    assert deleted["dropSlotSql"] == "SELECT pg_drop_replication_slot('iceberg_crm');" and "disk" in deleted["warning"]
    gone = stack.call("GET", path)
    assert gone.status_code == 404 and gone.json()["code"] == "unknown_replication"


def test_other_teams_see_nothing(stack):
    replication = ready(stack)
    run = stack.call("POST", f"/replications/{replication['id']}/runs", "bob", json={"wait": 30}).json()
    assert stack.call("GET", "/replications", "dave").json() == {"replications": []}
    listed = stack.call("GET", "/replications", "carol").json()
    assert list(listed) == ["replications"] and [r["id"] for r in listed["replications"]] == [replication["id"]]
    assert listed["replications"][0]["lastRun"]["id"] == run["id"] and listed["replications"][0]["role"] == "reader"
    assert stack.call("GET", f"/replications?team={OTHER}", "carol").json() == {"replications": []}
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
    # Not enabled, a schedule has no next run.
    paused = stack.call("PUT", path, json={"cron": "0 5 * * *", "enabled": False}).json()
    assert paused["enabled"] is False and paused["nextRunAt"] is None
    schedule = stack.call("PUT", path, json={"cron": "0 5 * * *"}).json()
    assert set(schedule) == {"cron", "enabled", "nextRunAt", "lastRun", "createdBy", "createdAt"}
    assert schedule["cron"] == "0 5 * * *" and schedule["enabled"] is True and schedule["lastRun"] is None
    assert schedule["createdBy"] == "alice" and seconds(schedule["nextRunAt"]) > seconds(schedule["createdAt"])
    assert stack.call("GET", f"/replications/{replication['id']}").json()["schedule"] == schedule
    scheduler_tick = __import__("replication.scheduler", fromlist=["Scheduler"]).Scheduler(stack.services).tick
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
    assert stack.call("GET", f"/replications/{replication['id']}").json()["schedule"]["lastRun"] == started[0]
    assert stack.call("DELETE", path).json() == {"deleted": True, "replication": replication["id"]}
    missing = stack.call("DELETE", path)
    assert missing.status_code == 404 and missing.json()["code"] == "no_schedule"


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


def test_every_error_has_the_same_body(stack, monkeypatch):
    def refused(response, status, code):
        body = response.json()
        assert response.status_code == status and body["code"] == code, response.text
        assert isinstance(body["error"], str) and body["error"] and set(body) <= {"error", "code", "detail"}

    refused(stack.call("GET", "/nothing-here"), 404, "not_found")
    refused(stack.client.get("/nothing/here"), 404, "not_found")
    refused(stack.call("PUT", "/me"), 405, "method_not_allowed")
    refused(stack.call("GET", "/me", "mallory"), 401, "unauthorized")
    refused(stack.call("GET", "/replications?environment=test"), 422, "invalid_request")
    refused(stack.call("GET", "/replications/rp-" + "0" * 24), 404, "unknown_replication")
    refused(stack.call("GET", "/runs/run-" + "0" * 24), 404, "unknown_run")
    refused(stack.call("POST", "/replications/validate", json={"sourceType": "postgres", "source": {"x": 1}}), 422,
            "invalid_replication")

    async def broken(caller):
        raise RuntimeError("boom")
    monkeypatch.setattr(stack.services, "whoami", broken)
    refused(stack.call("GET", "/me"), 500, "internal_error")
    assert ReplicationError(418, "x").code == "internal_error"
    assert ReplicationError(405, "x").code == "method_not_allowed"


def test_tools_return_what_the_api_returns(stack):
    """An MCP tool gives the REST operation's body, unchanged and without a wrapper of its own."""
    replication = ready(stack)
    id = replication["id"]
    check = stack.tool("check_source", "bob", replication=id)
    assert check["connected"] and check["tables"][0]["name"] == "public.orders"
    run = stack.tool("run_replication", "bob", replication=id, wait=30, full_refresh=True)
    assert run["status"] == "success" and run["fullRefresh"] and run["kind"] == "sync"
    assert [t["name"] for t in run["results"]] == ["public.orders", "public.customers"]
    schedule = stack.tool("set_schedule", replication=id, cron="*/15 * * * *")
    assert schedule == stack.call("GET", f"/replications/{id}").json()["schedule"]
    for tool, arguments, path in (
            ("whoami", {}, "/me"),
            ("list_environments", {"team": TEAM}, f"/teams/{TEAM}/environments"),
            ("list_templates", {}, "/templates"),
            ("list_replications", {}, "/replications"),
            ("list_replications", {"team": TEAM, "environment": "production"},
             f"/replications?team={TEAM}&environment=production"),
            ("get_replication", {"replication": id}, f"/replications/{id}"),
            ("list_runs", {"replication": id, "limit": 1}, f"/replications/{id}/runs?limit=1"),
            ("get_run", {"run": run["id"]}, f"/runs/{run['id']}"),
            ("get_run", {"run": run["id"], "wait": 5}, f"/runs/{run['id']}?wait=5"),
            ("get_run_logs", {"run": run["id"], "limit": 1}, f"/runs/{run['id']}/logs?limit=1")):
        assert stack.tool(tool, "carol", **arguments) == stack.call("GET", path, "carol").json(), tool
    assert list(stack.tool("list_runs", replication=id)) == ["runs"]
    assert stack.tool("get_run", run=run["id"]) == run
    assert stack.tool("get_replication", replication=id)["lastCheck"] == check
    assert stack.tool("get_run_logs", run=run["id"])["lines"] == ["Total records read: 3"]
    changed = stack.tool("update_replication", replication=id, description="CRM orders", expected_revision=2)
    assert changed["description"] == "CRM orders" and changed["revision"] == 3
    with pytest.raises(ToolRefused, match=r"\[revision_conflict\]"):
        stack.tool("update_replication", replication=id, description="again", expected_revision=2)
    removed = stack.tool("set_replication_secrets", replication=id, secrets={"password": None})
    assert removed["secretsMissing"] == ["password"]
    with pytest.raises(ToolRefused, match=r"\[secrets_missing\]"):
        stack.tool("run_replication", "bob", replication=id)
    with pytest.raises(ToolRefused, match=r"\[run_finished\]"):
        stack.tool("cancel_run", "bob", run=run["id"])
    assert stack.tool("delete_schedule", replication=id) == {"deleted": True, "replication": id}
    with pytest.raises(ToolRefused, match=r"\[no_schedule\]"):
        stack.tool("delete_schedule", replication=id)
    with pytest.raises(ToolRefused, match=r"\[confirmation_mismatch\]"):
        stack.tool("delete_replication", replication=id, confirm_name="nope")
    deleted = stack.tool("delete_replication", replication=id, confirm_name="crm")
    assert deleted["deleted"] is True and deleted["replication"] == id and "id" not in deleted
    assert "pg_drop_replication_slot" in deleted["dropSlotSql"]
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

    for tool in ("run_replication", "get_run"):
        wait = parameters(tool)["wait"]
        assert (wait["default"], wait["minimum"], wait["maximum"]) == (0, 0, 600), tool
    limit = parameters("get_run_logs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (500, 1, 5000)
    assert "tail" not in parameters("get_run_logs")
    limit = parameters("list_runs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 200)
    assert {"replication", "confirm_name"} == set(parameters("delete_replication"))
    assert {"expected_revision", "source_type"} <= set(parameters("update_replication"))
    assert "full_refresh" in parameters("run_replication")
    # A resource is named by its noun, never "id".
    assert not [name for name in tools if "id" in parameters(name)]
    read = (True, False, True, False)
    for tool in ("whoami", "list_environments", "list_templates", "list_replications", "get_replication",
                 "validate_replication", "list_runs", "get_run", "get_run_logs"):
        assert hints(tool) == read, tool
    # These can remove secrets and the saved position, or overwrite a value nobody can read back.
    for tool in ("update_replication", "set_replication_secrets", "delete_replication", "delete_schedule",
                 "disable_environment"):
        assert hints(tool) == (False, True, False, False), tool
    for tool in ("enable_environment", "create_replication", "cancel_run", "set_schedule"):
        assert hints(tool) == (False, False, False, False), tool
    # A run and a source check connect to the source, outside the platform.
    for tool in ("run_replication", "check_source"):
        assert hints(tool) == (False, False, False, True), tool
    assert "incomplete" in stack.client.portal.call(_instructions, stack.app)


def test_the_contract_describes_errors_and_the_shared_shapes(stack):
    schema = stack.app.openapi()
    schemas = schema["components"]["schemas"]
    assert {"Error", "Me", "Environment", "EnvironmentList", "Run", "RunList", "RunLogs", "CancelResult",
            "Schedule", "Deleted", "TemplateList", "ReplicationList"} <= set(schemas)
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
    assert answer("listTemplates") == "TemplateList" and answer("listReplications") == "ReplicationList"
    assert answer("runReplication", "202") == answer("getRun") == "Run" and answer("listRuns") == "RunList"
    assert answer("getRunLogs") == "RunLogs" and answer("cancelRun") == "CancelResult"
    assert answer("setSchedule") == "Schedule" and answer("createReplication", "201") == "Replication"
    assert answer("deleteReplication") == answer("deleteSchedule") == "Deleted"
    assert "results" in schemas["Run"]["properties"] and "results" not in schemas["RunItem"]["properties"]
    assert "incomplete" in schemas["RunItem"]["properties"]["status"]["enum"]
    assert operations["deleteReplication"][:2] == ("delete", "/api/v1/replications/{replication}")
    assert operations["setReplicationSecrets"][:2] == ("patch", "/api/v1/replications/{replication}/secrets")

    def query(id):
        return {p["name"]: p for p in operations[id][2]["parameters"] if p["in"] == "query"}

    assert query("deleteReplication")["confirmName"]["required"] is True
    assert query("getRun")["wait"]["schema"] == {"type": "integer", "maximum": 600, "minimum": 0, "default": 0,
                                                 "title": "Wait", "description": query("getRun")["wait"]["description"]}
    assert set(query("getRunLogs")) == {"limit"} and query("getRunLogs")["limit"]["schema"]["maximum"] == 5000
    assert set(schemas["RunInput"]["properties"]) == {"fullRefresh", "wait"}
    assert "expectedRevision" in schemas["ReplicationChange"]["properties"]
    # No path parameter is called id.
    assert not [path for path in schema["paths"] if "{id}" in path]


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


async def _instructions(app):
    from replication.mcp_server import INSTRUCTIONS
    return INSTRUCTIONS


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
        "source": SOURCE}).json()["code"] == "invalid_database"


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
