import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import dlt
import pytest
from pyiceberg.exceptions import CommitFailedException

from runner import entrypoint, guard, iceberg_io, iceberg_load, publish, sources
from runner.scrub import Scrubber


class Source(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/airports.csv":
            body, kind = b"code,name,country\nAMS,Schiphol,NL\nJFK,Kennedy,US\n", "text/csv"
        elif self.headers.get("Authorization") != "Bearer s3cr3t-value":
            self.send_response(401)
            self.end_headers()
            return
        else:
            page = int(parse_qs(url.query).get("page", ["1"])[0])
            rows = [{"id": i, "tags": [{"t": "x"}]} for i in range(1, 6)][(page - 1) * 2: page * 2]
            more = page * 2 < 5
            body = json.dumps({"data": rows, "next": f"{self.server.base}customers?page={page + 1}" if more else None})
            body, kind = body.encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    httpd.base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.base
    httpd.shutdown()


def resolver(monkeypatch, *addresses):
    results = [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443))
               for a in addresses]
    monkeypatch.setattr(guard, "_original", lambda *args, **kwargs: results)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "172.17.0.1", "169.254.169.254",
                                     "::1", "fd00::1", "::ffff:10.0.0.1", "100.64.0.1", "0.0.0.0", "224.0.0.1"])
def test_the_guard_refuses_private_addresses(monkeypatch, address):
    resolver(monkeypatch, "93.184.216.34", address)
    guard.install()
    try:
        with pytest.raises(guard.BlockedAddress):
            socket.getaddrinfo("api.example.com", 443)
    finally:
        guard.uninstall()


def test_the_guard_allows_public_hosts_and_the_platform_aliases(monkeypatch):
    resolver(monkeypatch, "93.184.216.34")
    guard.install()
    try:
        assert socket.getaddrinfo("api.example.com", 443)[0][4][0] == "93.184.216.34"
        resolver(monkeypatch, "172.18.0.5")
        assert socket.getaddrinfo("polaris-control-plane", 8181) and socket.getaddrinfo(b"rustfs", 9000)
        with pytest.raises(guard.BlockedAddress, match="private sources"):
            socket.getaddrinfo("host.docker.internal", 5432)
    finally:
        guard.uninstall()
    guard.install(allow_private=True)
    try:
        assert socket.getaddrinfo("host.docker.internal", 5432)
    finally:
        guard.uninstall()
    assert socket.getaddrinfo is guard._system


def test_scrubbing_masks_tokens_secrets_and_credentials_in_urls():
    scrub = Scrubber(["polaris-token-value", "s3cr3t-value", "abc"])
    text = scrub("token polaris-token-value, Bearer eyJ.x-y, url ?api_key=k1&x=1 postgresql://u:pw@db/x s3cr3t-value")
    assert "polaris-token-value" not in text and "s3cr3t-value" not in text and "eyJ" not in text
    assert "k1" not in text and ":pw@" not in text and "abc" not in Scrubber(["abc"]).values


def test_scrubbing_masks_encoded_secrets_and_failed_request_queries():
    scrub = Scrubber(["Ab12+cd/ef=gh"])
    text = scrub("404 Client Error: Not Found for url: https://api.example.test/v1/x?zz=Ab12%2Bcd%2Fef%3Dgh&page=2\n"
                 "k=Ab12%2Bcd/ef%3Dgh 'Ab12+cd/ef=gh' Authorization: Basic dXNlcjpBYjEyK2Nk")
    assert "Ab12" not in text and "dXNlcjpB" not in text
    assert "for url: https://api.example.test/v1/x?***" in text


def test_secrets_replace_their_placeholders_only():
    config = {"client": {"auth": {"token": "$secret:token"}, "base_url": "$secret:is-not-a-name"}, "x": ["$secret:a"]}
    assert sources.substitute(config, {"token": "t", "a": "A"}) == {
        "client": {"auth": {"token": "t"}, "base_url": "$secret:is-not-a-name"}, "x": ["A"]}
    with pytest.raises(sources.SourceError, match="not set"):
        sources.substitute({"password": "$secret:password"}, {})


def test_sql_sources_use_pure_python_drivers_and_tls_modes():
    engine = sources.engine({"dialect": "postgresql", "host": "db.example.com", "port": 5432, "database": "crm",
                             "username": "reader", "password": "p@ss/word", "tls": "verify-full"})
    assert engine.url.drivername == "postgresql+pg8000" and engine.url.password == "p@ss/word"
    assert sources.engine({"dialect": "mysql", "host": "h", "port": 3306, "database": "d"}).url.drivername == \
        "mysql+pymysql"
    required = sources.tls_args("postgresql", "require")["ssl_context"]
    assert required.check_hostname is False and sources.tls_args("mysql", "verify-full")["ssl"].check_hostname
    assert sources.tls_args("postgresql", "disable") == {}
    for bad in ({"dialect": "sqlite", "host": "h", "port": 1, "database": "d"},):
        with pytest.raises(sources.SourceError):
            sources.engine(bad)
    with pytest.raises(sources.SourceError):
        sources.tls_args("postgresql", "prefer")
    with pytest.raises(sources.SourceError, match="at least one table"):
        sources.sql_database({"dialect": "postgresql", "tables": []})


def load(tmp_path, resources, name="t"):
    pipeline = dlt.pipeline(f"test_{name}", destination=dlt.destinations.filesystem((tmp_path / "out").as_uri()),
                            dataset_name="raw", pipelines_dir=str(tmp_path / "pipelines"))
    pipeline.run(resources, loader_file_format="parquet")
    return {k: v for k, v in pipeline.last_trace.last_normalize_info.row_counts.items() if not k.startswith("_dlt")}


def test_a_rest_api_source_pages_with_its_secret(server, tmp_path):
    config = {"client": {"base_url": server, "auth": {"type": "bearer", "token": "$secret:token"},
                         "paginator": {"type": "json_link", "next_url_path": "next"}},
              "resources": [{"name": "customers", "endpoint": {"path": "customers", "data_selector": "data"}}]}
    built = sources.build("rest_api", config, {"token": "s3cr3t-value"}, tmp_path)
    resources = sources.apply_hints(built, {"customers": {"writeDisposition": "merge", "primaryKey": ["id"]}})
    assert resources[0].write_disposition == {"disposition": "merge", "strategy": "upsert"}
    assert load(tmp_path, resources, "rest") == {"customers": 5, "customers__tags": 5}


def test_a_csv_file_over_http_is_read_with_arrow(server, tmp_path):
    built = sources.build("filesystem", {"url": server + "airports.csv", "format": "csv", "table": "airports"}, {},
                          tmp_path)
    assert [r.name for r in built] == ["airports"]
    assert load(tmp_path, sources.apply_hints(built, {}), "csv") == {"airports": 2}
    with pytest.raises(sources.SourceError, match="ING_MAX_DOWNLOAD_GB"):
        sources.build("filesystem", {"url": server + "airports.csv", "format": "csv", "table": "airports"}, {},
                      tmp_path / "small", max_download=10)
    signed = sources.build("filesystem", {"url": "$secret:file_url", "format": "csv", "table": "airports"},
                           {"file_url": server + "airports.csv?X-Amz-Signature=abc"}, tmp_path / "signed")
    assert load(tmp_path / "signed", sources.apply_hints(signed, {}), "signed") == {"airports": 2}
    with pytest.raises(sources.SourceError, match="kept in a secret"):
        sources.build("filesystem", {"url": "$secret:file_url", "format": "csv", "table": "x"},
                      {"file_url": "s3://bucket/x.csv"}, tmp_path)
    with pytest.raises(sources.SourceError, match="https, http or s3"):
        sources.build("filesystem", {"url": "file:///etc/passwd", "format": "csv", "table": "x"}, {}, tmp_path)
    with pytest.raises(sources.SourceError, match="format"):
        sources.build("filesystem", {"url": server, "format": "xlsx", "table": "x"}, {}, tmp_path)
    with pytest.raises(sources.SourceError, match="Unknown source"):
        sources.build("python", {}, {}, tmp_path)


def test_progress_logs_each_step_and_a_heartbeat_with_memory(tmp_path):
    import io
    import time

    from runner.progress import Progress

    @dlt.resource(name="orders")
    def orders():
        time.sleep(0.3)
        yield [{"id": i} for i in range(3)]

    stream = io.StringIO()
    progress = Progress(interval=0.1, stream=stream)
    pipeline = dlt.pipeline("test_progress", destination=dlt.destinations.filesystem((tmp_path / "out").as_uri()),
                            dataset_name="raw", pipelines_dir=str(tmp_path / "pipelines"), progress=progress)
    try:
        pipeline.run(orders(), loader_file_format="parquet")
    finally:
        progress.close()
    log = stream.getvalue()
    for step in ("Extract", "Normalize", "Load"):
        assert re.search(rf"{step} test_progress.* started", log) and re.search(rf"{step} .*finished in", log)
    assert re.search(r"\] Extract test_progress running, memory \d+\.\d\d GB", log)


def test_iceberg_loads_in_pieces_in_one_commit(tmp_path, monkeypatch):
    import pyarrow as pa
    from dlt.common.libs.pyiceberg import get_iceberg_tables
    from dlt.destinations.adapters import iceberg_adapter
    from dlt.destinations.impl.filesystem.filesystem import IcebergLoadFilesystemJob

    monkeypatch.setattr(IcebergLoadFilesystemJob, "run", IcebergLoadFilesystemJob.run)  # undone after the test
    iceberg_load.install(size=1)  # a piece per batch
    read = []
    pieces = iceberg_load.pieces
    monkeypatch.setattr(iceberg_load, "pieces", lambda *a: (read.append(p.num_rows) or p for p in pieces(*a)))

    def rows(n):
        for start in range(0, n, 70_000):
            ids = list(range(start, min(start + 70_000, n)))
            yield pa.table({"id": ids, "part": [i % 3 for i in ids]})

    pipeline = dlt.pipeline("test_pieces", destination=dlt.destinations.filesystem((tmp_path / "out").as_uri()),
                            dataset_name="raw", pipelines_dir=str(tmp_path / "pipelines"))
    for name, partition in (("plain", None), ("parted", "part")):
        def load(n, disposition, name=name, partition=partition):
            resource = dlt.resource(rows(n), name=name, write_disposition=disposition)
            pipeline.run(iceberg_adapter(resource, partition=partition) if partition else resource,
                         table_format="iceberg")
            table = get_iceberg_tables(pipeline, name)[name]
            return table.scan().to_arrow().num_rows, table

        assert load(200_000, "replace")[0] == 200_000
        read.clear()
        count, table = load(150_000, "append")
        assert count == 350_000 and len(read) > 2
        assert table.metadata.snapshots[-1].summary["added-records"] == "150000"
        commits = len(table.metadata.metadata_log)
        count, table = load(100_000, "replace")
        assert count == 100_000 and len(table.metadata.metadata_log) == commits + 1
        assert load(0, "replace")[0] == 0

    def failing(*args):
        yield from pieces(*args)
        raise OSError("storage went away")

    monkeypatch.setattr(iceberg_load, "pieces", failing)
    with pytest.raises(Exception, match="storage went away"):
        load(100_000, "append")
    assert get_iceberg_tables(pipeline, "parted")["parted"].scan().to_arrow().num_rows == 0


def test_hints_come_from_the_pipeline_and_full_refresh_replaces():
    @dlt.resource(name="orders")
    def orders():
        yield {"id": 1}

    with pytest.raises(sources.SourceError, match="primary key"):
        sources.apply_hints([orders()], {"orders": {"writeDisposition": "merge"}})
    merged = sources.apply_hints([orders()], {"orders": {"writeDisposition": "merge", "primaryKey": ["id"]}})[0]
    assert merged.compute_table_schema()["x-merge-strategy"] == "upsert"
    refreshed = sources.apply_hints([orders()], {"orders": {"writeDisposition": "merge", "primaryKey": ["id"]}},
                                    full_refresh=True)[0]
    assert refreshed.write_disposition == "replace"
    partitioned = sources.apply_hints([orders()], {"orders": {"partition": ["id"]}})[0]
    assert partitioned.write_disposition == "append" and "x-iceberg-partition" in json.dumps(
        partitioned.compute_table_schema(), default=str)


class FakeTransaction:
    def __init__(self, table, conflicts):
        self.table, self.conflicts = table, conflicts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None and self.conflicts:
            self.conflicts.pop()
            self.table.properties.clear()  # Nothing of a failed commit lands.
            raise CommitFailedException("409: a concurrent commit changed the table")
        return False

    def set_properties(self, values):
        self.table.properties.update(values)


class FakeCatalog:
    def __init__(self, conflicts=0):
        self.tables, self.conflicts, self.loads = {}, [True] * conflicts, 0

    def load_table(self, identifier):
        self.loads += 1
        if identifier[1] == "missing":
            raise RuntimeError("no such table")
        table = self.tables.setdefault(identifier, SimpleNamespace(properties={}))
        table.transaction = lambda: FakeTransaction(table, self.conflicts)
        return table


def test_publish_stamps_the_bridge_properties_and_reports_errors():
    job = {"namespace": "raw_crm", "url": "http://localhost:3008/#/pipelines/pl-1", "sourceType": "sql_database"}
    cursor = {"incremental": {"updated_at": {"last_value": "2026-10-01"}}}
    state = {"sources": {"crm": {"resources": {"customers": cursor}}}}
    catalog = FakeCatalog()
    result = publish.main(job, catalog, {"customers": 12, "customers__tags": 0, "missing": 1}, "171.1", "1.31.0",
                          state, {"customers__tags": "customers"})
    assert result == {"tables": ["raw_crm.customers", "raw_crm.customers__tags"],
                      "errors": [{"table": "raw_crm.missing", "error": "RuntimeError"}]}
    props = {k.removeprefix("iceberg-data-platform."): v
             for k, v in catalog.tables[("raw_crm", "customers")].properties.items()}
    assert props["producer"] == "dlt" and props["producer.version"] == "dlt 1.31.0"
    assert props["producer.url"] == job["url"] and props["producer.run-id"] == "171.1"
    assert props["producer.node"] == "sql_database.customers" and props["producer.source-revision"] == "2026-10-01"
    assert props["quality.status"] == "pass" and props["quality.summary"] == "12 rows loaded by dlt"
    child = catalog.tables[("raw_crm", "customers__tags")].properties
    assert child["iceberg-data-platform.quality.status"] == "warn"
    assert child["iceberg-data-platform.producer.source-revision"] == "2026-10-01"
    assert not any(word in key for key in props for word in ("token", "secret", "password", "credential"))


def test_publish_sets_the_properties_again_after_a_conflicting_commit(monkeypatch):
    waits = []
    monkeypatch.setattr(iceberg_io.time, "sleep", waits.append)
    job = {"namespace": "raw_crm", "url": "http://localhost:3008/#/pipelines/pl-1", "sourceType": "sql_database"}
    catalog = FakeCatalog(conflicts=2)
    result = publish.main(job, catalog, {"customers": 12}, "171.1", "1.31.0", {}, {})
    assert result == {"tables": ["raw_crm.customers"], "errors": []}
    assert catalog.loads == 3 and len(waits) == 2
    assert catalog.tables[("raw_crm", "customers")].properties["iceberg-data-platform.producer"] == "dlt"


def test_publish_gives_up_on_a_table_that_keeps_conflicting(monkeypatch):
    monkeypatch.setattr(iceberg_io.time, "sleep", lambda wait: None)
    job = {"namespace": "raw_crm", "url": "http://localhost:3008/#/pipelines/pl-1", "sourceType": "sql_database"}
    catalog = FakeCatalog(conflicts=10)
    result = publish.main(job, catalog, {"customers": 12}, "171.1", "1.31.0", {}, {})
    assert result == {"tables": [], "errors": [{"table": "raw_crm.customers", "error": "CommitFailedException"}]}
    assert catalog.loads == 5


def test_state_is_restored_unless_refreshing_and_kept_after_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint, "WORK", tmp_path)
    (tmp_path / "state" / "schemas").mkdir(parents=True)
    (tmp_path / "state" / "state.json").write_text("{}")
    (tmp_path / "state" / "schemas" / "s.schema.json").write_text("{}")
    folder = tmp_path / "pipelines" / "pl_x"
    assert not entrypoint.restore({"fullRefresh": True}, folder) and not folder.exists()
    assert entrypoint.restore({}, folder) and (folder / "schemas" / "s.schema.json").exists()
    entrypoint.keep(folder, tmp_path / "out")
    assert (tmp_path / "out" / "state" / "state.json").exists()
    assert entrypoint.pipeline_name({"pipelineId": "pl-abc"}) == "pl_abc"


def test_a_failed_run_writes_a_scrubbed_result(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint, "WORK", tmp_path)
    monkeypatch.setenv("ING_TOKEN", "polaris-token-value")
    monkeypatch.setenv("ING_SECRETS", json.dumps({"password": "s3cr3t-value"}))
    (tmp_path / "job.json").write_text(json.dumps({
        "runId": "run-" + "a" * 24, "pipelineId": "pl-1", "namespace": "raw", "warehouse": "db-1",
        "catalogUri": "http://polaris-control-plane:8181/api/catalog", "storageEndpoint": "http://rustfs:9000",
        "url": "http://localhost:3008/#/pipelines/pl-1", "sourceType": "filesystem", "resources": {},
        "source": {"url": "ftp://user:s3cr3t-value@host/x", "format": "csv", "table": "t"}}))
    monkeypatch.setattr(guard, "install", lambda allow=False: None)
    try:
        assert entrypoint.main() == 1
    finally:
        for key in ("DESTINATION__FILESYSTEM__BUCKET_URL", "ICEBERG_CATALOG__ICEBERG_CATALOG_TYPE",
                    "ICEBERG_CATALOG__ICEBERG_CATALOG_CONFIG", "ICEBERG_S3_ENDPOINT", "RESTORE_FROM_DESTINATION"):
            monkeypatch.delenv(key, raising=False)
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["status"] == "failed" and "https, http or s3" in result["error"]
    log = (tmp_path / "out" / "run.log").read_text()
    assert "SourceError" in log and "s3cr3t-value" not in log and "polaris-token-value" not in log
    assert "ING_TOKEN" not in __import__("os").environ and not (tmp_path / "out" / "state").exists()


def test_child_tables_keep_their_root_key_whatever_the_write_disposition():
    # A full refresh replaces what a merge loaded: child tables must keep _dlt_root_id either way.
    import dlt

    from runner.entrypoint import platform_source
    source = platform_source("pl_x", [dlt.resource([{"id": 1, "tags": [{"t": "a"}]}], name="customers")])
    assert source.name == "pl_x" and source.root_key and list(source.resources) == ["customers"]


def vended(key, expires_in):
    import time

    return {"s3.access-key-id": key, "s3.secret-access-key": f"secret-{key}", "s3.session-token": f"token-{key}",
            "s3.session-token-expires-at-ms": str(int((time.time() + expires_in) * 1000))}


class CredentialsCatalog:
    def __init__(self, response):
        self.response, self.calls = response, []

    def load_credentials(self, identifier, location):
        self.calls.append((identifier, location))
        if isinstance(response := self.response, Exception):
            raise response
        return response


def test_a_long_load_renews_the_tables_storage_credentials_before_they_expire(monkeypatch):
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")
    catalog = CredentialsCatalog(vended("new", 3600))
    io = iceberg_io.InternalS3FileIO({**vended("old", 60), "s3.endpoint": "http://localhost:9000"})
    io.renew_from(catalog, ("raw_crm", "customers"), "s3://bucket/raw_crm/customers/metadata/1.json")
    io.new_output("s3://bucket/raw_crm/customers/data/1.parquet")
    assert catalog.calls == [(("raw_crm", "customers"), "s3://bucket/raw_crm/customers/metadata/1.json")]
    assert io.properties["s3.access-key-id"] == "new" and io.properties["s3.endpoint"] == "http://rustfs:9000"
    io.new_output("s3://bucket/raw_crm/customers/data/2.parquet")
    assert len(catalog.calls) == 1


def test_a_failed_renewal_keeps_the_credentials_and_logs_no_secret(monkeypatch, caplog):
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")
    catalog = CredentialsCatalog(RuntimeError("token-old leaked in a provider message"))
    io = iceberg_io.InternalS3FileIO(vended("old", 60))
    io.renew_from(catalog, ("raw_crm", "customers"), "s3://bucket/raw_crm/customers/metadata/1.json")
    assert not io.renew_credentials()
    assert io.properties["s3.access-key-id"] == "old"
    assert "token-old" not in caplog.text and "raw_crm.customers" in caplog.text
    assert not io.renew_credentials() and len(catalog.calls) == 1  # Retried later, not on every file.


def test_the_run_binds_rest_catalog_tables_to_their_fileio():
    from pyiceberg.catalog.rest import RestCatalog

    assert getattr(RestCatalog._response_to_table, "renews_credentials", False)
