"""The run image's start-up program, with a fake OLake command line in place of the real one."""

import json
import sys
import textwrap

import pytest

from runner import entrypoint, olake, publish
from runner.scrub import Scrubber

PASSWORD = "pg-very-secret-password"
TOKEN = "catalog-token-abcdefgh"
STREAMS = {"streams": [
    {"stream": {"name": "orders", "namespace": "public", "source_defined_primary_key": ["id"],
                "available_cursor_fields": ["id", "updated_at"], "supported_sync_modes": ["cdc", "strict_cdc",
                                                                                         "incremental", "full_refresh"],
                "type_schema": {"properties": {"id": {}, "updated_at": {}}}}},
    {"stream": {"name": "audit_log", "namespace": "public", "source_defined_primary_key": [],
                "supported_sync_modes": ["cdc", "strict_cdc", "full_refresh"], "type_schema": {"properties": {}}}},
]}
FAKE = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, os, pathlib, signal, sys, time
    args = sys.argv[1:]
    config = pathlib.Path(args[args.index("--config") + 1])
    source = json.loads(config.read_text())
    print("\\x1b[32mINFO\\x1b[0m connecting with " + source["password"], flush=True)
    mode = os.environ.get("FAKE_MODE", "")
    if args[0] == "check":
        print('INFO {{"connectionStatus":{{"status":"FAILED","message":"required CDC prerequisites not met: '
              'publication","prerequisites":[{{"name":"publication","required":true,"passed":false,'
              '"current_value":"not found","recommended_value":"exists","description":"d"}}]}},'
              '"type":"CONNECTION_STATUS"}}', flush=True)
    elif args[0] == "discover":
        (config.parent / "available_streams.json").write_text(json.dumps({json.dumps(STREAMS)}))
    elif args[0] == "sync":
        state = pathlib.Path(args[args.index("--state") + 1])
        selected = json.loads(pathlib.Path(args[args.index("--selected-streams") + 1]).read_text())
        pathlib.Path(os.environ["SEEN"]).write_text(json.dumps({{
            "selected": selected, "state": state.read_text(), "telemetry": os.environ.get("TELEMETRY_DISABLED"),
            "token": json.loads(pathlib.Path(args[args.index("--destination") + 1]).read_text())["writer"]["token"],
            "env": sorted(k for k in os.environ if k.startswith("REP_"))}}))
        if mode == "slow":
            def stop(*_):
                chunks = {{"chunks": [{{"min": 5}}]}}
                state.write_text(json.dumps({{"global": {{"state": {{"lsn": "0/AB"}}}},
                                              "streams": [{{"stream": "orders", "state": chunks}}]}}))
                sys.exit(1)
            signal.signal(signal.SIGTERM, stop)
            time.sleep(60)
        if mode == "fail":
            print("2026 FATAL failed to connect: password authentication failed", flush=True)
            sys.exit(1)
        state.write_text(json.dumps({{"global": {{"state": {{"lsn": "0/CD"}}}},
                                      "streams": [{{"stream": "orders", "state": {{"chunks": []}}}}]}}))
        print("INFO Total records read: 12 | Total bytes read: 374 B", flush=True)
""")


def job(**fields):
    return {"runId": "run-" + "a" * 24, "kind": "sync", "replicationId": "rp-1", "replicationName": "crm",
            "url": "http://localhost:3014/#/replications/rp-1", "warehouse": "db-1", "namespace": "raw_crm",
            "catalogUri": "http://polaris-control-plane:8181/api/catalog", "storageEndpoint": "http://rustfs:9000",
            "publicStorageEndpoint": "http://localhost:9000", "sourceType": "postgres",
            "source": {"host": "db.example.com", "port": 5432, "database": "crm", "username": "olake_reader",
                       "password": "$secret:password", "tls": "verify-full", "slot": "s", "publication": "p",
                       "maxThreads": 4},
            "tables": {"public.orders": {"mode": "cdc", "table": "orders", "partition": "/{created_at,month}",
                                         "appendOnly": False},
                       "public.audit_log": {"mode": "cdc", "table": "audit_log", "appendOnly": False}},
            "options": {"deleteMode": "pos", "firstRun": "copy"}, "fullRefresh": False, "deadline": 300, **fields}


def test_olake_configuration():
    source = olake.source_config(job(), {"password": PASSWORD})
    assert source["password"] == PASSWORD and source["ssl"]["mode"] == "verify-full"
    assert source["update_method"] == {"replication_slot": "s", "publication": "p", "initial_wait_time": 120,
                                       "preserve_toast_values": True}
    no_cdc = job(source={**job()["source"], "slot": None, "publication": None})
    assert "update_method" not in olake.source_config(no_cdc, {"password": PASSWORD})
    writer = olake.destination_config(job(), TOKEN, "http://127.0.0.1:18181/api/catalog")["writer"]
    assert writer["iceberg_s3_path"] == "db-1" and writer["iceberg_db"] == "raw_crm" and writer["token"] == TOKEN
    assert writer["rest_access_delegation"] == "vended-credentials" and writer["s3_endpoint"] == "http://rustfs:9000"


def test_tls_and_large_values():
    pem = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"
    # verify-full is the default; OLake needs a CA for it: the public authorities, or the source's own.
    assert olake.ssl_config({}) == {"mode": "verify-full", "cert_source": "path",
                                    "server_ca_path": "/etc/ssl/certs/ca-certificates.crt"}
    assert olake.ssl_config({"tls": "verify-ca", "caCertificate": pem}) == {"mode": "verify-ca", "server_ca": pem}
    assert olake.ssl_config({"tls": "require", "caCertificate": pem}) == {"mode": "require"}
    # Unchanged large values are read back from the table instead of landing as __olake_unavailable_value__.
    assert olake.source_config(job(), {"password": PASSWORD})["update_method"]["preserve_toast_values"] is True


def test_selected_streams():
    streams = olake.available_from(STREAMS)
    selected = olake.selected_streams(job(), streams)["selected_streams"]["public"]
    orders, audit = sorted(selected, key=lambda s: s["stream_name"], reverse=True)
    assert orders == {"stream_name": "orders", "sync_mode": "cdc", "partition_regex": "/{created_at,month}",
                      "normalization": True, "append_mode": False, "update_type": "pos",
                      "destination_database": "raw_crm", "destination_table": "orders"}
    # A table without a primary key can only be appended to.
    assert audit["append_mode"] is True
    changes_only = olake.selected_streams(job(options={"deleteMode": "dv", "firstRun": "changes"}), streams)
    assert {s["sync_mode"] for s in changes_only["selected_streams"]["public"]} == {"strict_cdc"}
    assert {s["update_type"] for s in changes_only["selected_streams"]["public"]} == {"dv"}
    # Copy everything again copies the existing rows, also when the first run only followed changes.
    again = olake.selected_streams(job(options={"deleteMode": "pos", "firstRun": "changes"}, fullRefresh=True), streams)
    assert {s["sync_mode"] for s in again["selected_streams"]["public"]} == {"cdc"}
    incremental = job(tables={"public.orders": {"mode": "incremental", "cursor": "updated_at", "table": "orders"}})
    assert olake.selected_streams(incremental, streams)["selected_streams"]["public"][0]["cursor_field"] == "updated_at"
    with pytest.raises(RuntimeError, match=r"no table public\.missing"):
        olake.selected_streams(job(tables={"public.missing": {"mode": "cdc", "table": "missing"}}), streams)
    with pytest.raises(RuntimeError, match="cannot be synced"):
        olake.selected_streams(job(tables={"public.audit_log": {"mode": "incremental", "cursor": "at",
                                                                "table": "audit_log"}}), streams)


def test_messages_state_and_counts():
    text = '\x1b[90m2026\x1b[0m \x1b[32mINFO\x1b[0m {"connectionStatus":{"status":"SUCCEEDED"}}\nINFO other'
    assert olake.messages(text, "connectionStatus") == [{"status": "SUCCEEDED"}]
    state = {"global": {"state": {"lsn": "0/1A"}}, "streams": [{"state": {"chunks": [{"min": 1}]}}]}
    assert olake.position(state) == "0/1A" and not olake.first_copy_done(state)
    assert olake.first_copy_done({"streams": [{"state": {"chunks": []}}]}) and olake.first_copy_done({})
    assert olake.records_read("Total records read: 3\nTotal records read: 12 | x") == 12
    metadata = {"snapshots": [
        {"timestamp-ms": 100, "summary": {"added-records": "5"}},
        {"timestamp-ms": 200, "summary": {"added-records": "7", "added-position-deletes": "2",
                                          "added-delete-files": "1", "added-data-files": "1"}},
        {"timestamp-ms": 300, "summary": {"added-records": "1", "added-dvs": "1"}}]}
    assert publish.counts(metadata, 150) == {"records": 8, "replaced": 2, "dataFiles": 1, "deleteFiles": 2,
                                             "snapshots": 2}
    values = publish.properties(job(), "public.orders", publish.counts(metadata, 150), "0/1A", "v1.2.0", "now")
    assert values["iceberg-data-platform.producer"] == "olake"
    assert values["iceberg-data-platform.producer.source-revision"] == "LSN 0/1A"
    assert not any(word in key for key in values for word in ("token", "secret", "password"))


@pytest.fixture
def work(tmp_path, monkeypatch):
    fake = tmp_path / "olake"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    folder = tmp_path / "work"
    (folder / "state").mkdir(parents=True)
    monkeypatch.setattr(entrypoint, "WORK", folder)
    monkeypatch.setattr(entrypoint, "OLAKE", str(fake))
    monkeypatch.setenv("SEEN", str(tmp_path / "seen.json"))
    monkeypatch.setenv("REP_TOKEN", TOKEN)
    monkeypatch.setenv("REP_SECRETS", json.dumps({"password": PASSWORD}))
    published = []

    def fake_publish(job, token, since, position, version):
        published.append((token, position))
        return [{"name": n, "table": s["table"], "records": 1, "replaced": 0, "deleteFiles": 0}
                for n, s in job["tables"].items()], {"tables": list(job["tables"]), "errors": []}

    monkeypatch.setattr(publish, "main", fake_publish)
    return folder, tmp_path / "seen.json", published


def run(folder, **fields):
    (folder / "job.json").write_text(json.dumps(job(**fields)))
    code = entrypoint.main()
    return code, json.loads((folder / "out" / "result.json").read_text()), (folder / "out" / "run.log").read_text()


def test_a_check_reports_prerequisites_and_tables(work):
    folder, _, _ = work
    code, result, log = run(folder, kind="check", tables={})
    assert code == 0 and result["status"] == "success" and result["connected"] and not result["ready"]
    assert result["checks"][0]["name"] == "publication" and not result["checks"][0]["passed"]
    assert [s["name"] for s in result["streams"]] == ["public.audit_log", "public.orders"]
    assert result["streams"][1]["primaryKey"] == ["id"] and PASSWORD not in log and "***" in log
    assert not (folder / "olake" / "source.json").exists()


def test_a_sync_saves_state_and_never_leaks_secrets(work):
    folder, seen, published = work
    (folder / "state" / "state.json").write_text('{"global": {"state": {"lsn": "0/01"}}}')
    code, result, log = run(folder)
    assert code == 0 and result["status"] == "success" and result["readUpTo"] == "0/CD"
    assert result["firstCopyComplete"] and result["recordsRead"] == 12 and published == [(TOKEN, "0/CD")]
    assert json.loads((folder / "out" / "state" / "state.json").read_text())["global"]["state"]["lsn"] == "0/CD"
    observed = json.loads(seen.read_text())
    assert observed["state"] == '{"global": {"state": {"lsn": "0/01"}}}' and observed["telemetry"] == "true"
    assert observed["token"] == TOKEN and observed["env"] == []
    assert PASSWORD not in log and TOKEN not in log
    for name in ("source.json", "destination.json"):
        assert not (folder / "olake" / name).exists()


def test_a_full_refresh_starts_without_state(work):
    folder, seen, _ = work
    (folder / "state" / "state.json").write_text('{"global": {"state": {"lsn": "0/01"}}}')
    run(folder, fullRefresh=True)
    assert json.loads(seen.read_text())["state"] == "{}"


def test_the_deadline_stops_olake_and_the_next_run_continues(work, monkeypatch):
    folder, _, _ = work
    monkeypatch.setenv("FAKE_MODE", "slow")
    code, result, log = run(folder, deadline=1)
    assert code == 0 and result["status"] == "partial" and result["stoppedAtLimit"]
    assert not result["firstCopyComplete"] and "Time limit reached" in log
    saved = json.loads((folder / "out" / "state" / "state.json").read_text())
    assert saved["streams"][0]["state"]["chunks"] == [{"min": 5}]


def test_a_failed_sync_reports_olakes_error(work, monkeypatch):
    folder, _, _ = work
    monkeypatch.setenv("FAKE_MODE", "fail")
    code, result, _ = run(folder)
    assert code == 1 and result["status"] == "failed" and "password authentication failed" in result["error"]
    assert not (folder / "out" / "state").exists()


def test_scrubber_masks_urls_and_values():
    scrub = Scrubber([PASSWORD])
    assert scrub(f"postgres://u:{PASSWORD}@h/db password={PASSWORD}") == "postgres://u:***@h/db password=***"
