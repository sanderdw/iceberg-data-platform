import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import dlt
import pytest

from runner import entrypoint, guard, publish, sources
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
    with pytest.raises(sources.SourceError, match="https, http or s3"):
        sources.build("filesystem", {"url": "file:///etc/passwd", "format": "csv", "table": "x"}, {}, tmp_path)
    with pytest.raises(sources.SourceError, match="format"):
        sources.build("filesystem", {"url": server, "format": "xlsx", "table": "x"}, {}, tmp_path)
    with pytest.raises(sources.SourceError, match="Unknown source"):
        sources.build("python", {}, {}, tmp_path)


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
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_properties(self, values):
        self.table.properties.update(values)


class FakeCatalog:
    def __init__(self):
        self.tables = {}

    def load_table(self, identifier):
        if identifier[1] == "missing":
            raise RuntimeError("no such table")
        table = self.tables.setdefault(identifier, SimpleNamespace(properties={}))
        table.transaction = lambda: FakeTransaction(table)
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
