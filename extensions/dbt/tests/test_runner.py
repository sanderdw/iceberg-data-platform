import dataclasses
import json

import pytest

from dbt_portal import profiles
from dbt_portal.config import ROOT
from dbt_portal.errors import DbtError
from dbt_portal.runs import catalog_proxy as catalog_proxy_job
from dbt_portal.runs import forwarding, time_limit
from runner import catalog_proxy, entrypoint, publish, semantic
from tests.conftest import settings, write_info_schema


def test_arguments_allow_only_dbt_commands_and_add_platform_flags():
    args = entrypoint.arguments({"command": ["build", "--select", "a+"], "environment": "production"})
    assert args[:4] == ["dbt", "build", "--select", "a+"]
    assert args[args.index("--target") + 1] == "production" and "--generate-info-schema" in args
    assert entrypoint.arguments({"command": ["docs", "generate"], "environment": "development"})[-2:][0] == \
        "--output-dir"
    for command in (["debug"], ["run-operation", "drop"], ["docs", "serve"], []):
        with pytest.raises(SystemExit):
            entrypoint.arguments({"command": command, "environment": "development"})


def test_show_rows_are_parsed_from_the_log():
    log = "   dbt-oss 2.0.5\n[{\"id\":1,\"name\":\"A\"}]\n Succeeded model a\n"
    assert entrypoint.show_rows(log) == [{"id": 1, "name": "A"}]
    assert entrypoint.show_rows("no rows") is None


def test_catalogs_are_named_after_databases_and_clashes_refused():
    scope = {"databases": [{"id": "db-1", "name": "raw-data"}, {"id": "db-2", "name": "sales"}]}
    text, mapping = profiles.catalogs(scope, "http://polaris-control-plane:8181/api/catalog")
    assert mapping == {"raw_data": "db-1", "sales": "db-2"}
    assert "catalog_database: raw_data" in text and "access_delegation_mode: vended_credentials" in text
    with pytest.raises(DbtError) as clash:
        profiles.catalogs({"databases": [{"id": "a", "name": "x-y"}, {"id": "b", "name": "x_y"}]}, "uri")
    assert clash.value.code == "catalog_clash"
    assert "{{ env_var('DBT_ENV_SECRET_POLARIS_TOKEN') }}" in profiles.profiles("development")


def test_storage_is_forwarded_only_for_loopback_endpoints():
    discovery = {"storage": {"endpoint": "http://localhost:9000", "internalEndpoint": "http://rustfs:9000"}}
    assert forwarding(discovery) == {"listen": 9000, "target": "rustfs:9000"}
    discovery["storage"]["endpoint"] = "https://s3.example.org"
    assert forwarding(discovery) is None


def test_publish_stamps_the_bridge_table_property_conventions(tmp_path):
    write_info_schema(tmp_path / "info_schema" / "v1")
    nodes, columns, parents, tests = publish.load(tmp_path)
    job = {"catalogs": {"sales": "db-1", "raw_data": "db-3"}, "origin": "http://localhost:3004",
           "project": "prj-x", "run": "run-y", "revision": "abc", "environment": "development",
           "version": "iceberg-dbt 0.1.0"}
    results = {"model.shop.stg_regions": "success", "test.shop.unique_stg_regions_region_id": "pass"}
    values = publish.properties(job, nodes["model.shop.stg_regions"], nodes, parents, results, tests, "now")
    key = "iceberg-data-platform."
    assert values[key + "producer"] == "dbt" and values[key + "producer.source-revision"] == "abc"
    assert values[key + "producer.url"].startswith("http://localhost:3004/#/projects/prj-x/pipeline?")
    assert json.loads(values[key + "lineage.inputs"]) == [
        {"database": "db-1", "namespace": ["seeds"], "name": "regions"},
        {"database": "db-3", "namespace": ["raw"], "name": "events"}]
    assert (values[key + "quality.status"], values[key + "quality.summary"]) == ("pass", "1 passed")
    assert values["comment"] == "Cleaned regions"
    assert columns["model.shop.stg_regions"] == {"region_id": "Stable id"}
    assert not any(word in k for k in values for word in ("token", "secret", "password", "credential"))
    failing = publish.properties(job, nodes["model.shop.stg_regions"], nodes, parents,
                                 {**results, "test.shop.unique_stg_regions_region_id": "fail"}, tests, "now")
    assert failing[key + "quality.status"] == "fail"
    current = {key + "quality.status": "pass", key + "quality.summary": "1 passed", key + "owner": "x"}
    # Tests that did not run this time (dbt run) leave the last outcome; a model without tests loses it.
    assert publish.stale_quality("model.shop.stg_regions", tests, current) == []
    assert publish.stale_quality("model.shop.untested", tests, current) == [key + "quality.status",
                                                                           key + "quality.summary"]


def test_replace_table_ends_statements_after_the_model_sql():
    # A model may end with a -- comment, which would swallow a terminator on the same line.
    macro = (ROOT / "platform_macros" / "replace_table.sql").read_text()
    assert all(not line.rstrip().endswith("{{ sql }};") for line in macro.splitlines())


def test_a_public_storage_address_goes_through_the_catalog_proxy():
    discovery = {"catalog": {"internalUri": "http://polaris-control-plane:8181/api/catalog"},
                 "storage": {"endpoint": "http://localhost:9000", "internalEndpoint": "http://rustfs:9000"}}
    assert catalog_proxy_job(discovery) is None
    discovery["storage"]["endpoint"] = "https://share.example.com"
    assert forwarding(discovery) is None
    assert catalog_proxy_job(discovery) == {
        "listen": 18181, "upstream": "http://polaris-control-plane:8181",
        "uri": "http://127.0.0.1:18181/api/catalog",
        "storage": {"public": "https://share.example.com", "internal": "http://rustfs:9000"}}


def test_catalog_proxy_points_vended_storage_at_internal_storage():
    import http.client
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = {}

    class Catalog(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen["path"], seen["auth"] = self.path, self.headers["Authorization"]
            body = json.dumps({"metadata-location": "s3://b/m.json", "config": {
                "s3.endpoint": "https://share.example.com/", "s3.access-key-id": "k"},
                "storage-credentials": [{"prefix": "s3://b", "config": {"s3.endpoint": "https://share.example.com"}}],
                "other": "https://share.example.com"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Catalog)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    proxy = catalog_proxy.start(0, f"http://127.0.0.1:{upstream.server_port}", "https://share.example.com",
                                "http://rustfs:9000")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=10)
        connection.request("GET", "/api/catalog/v1/db/namespaces/a/tables/b", headers={"Authorization": "Bearer t"})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert seen == {"path": "/api/catalog/v1/db/namespaces/a/tables/b", "auth": "Bearer t"}
        assert body["config"] == {"s3.endpoint": "http://rustfs:9000", "s3.access-key-id": "k"}
        assert body["storage-credentials"][0]["config"]["s3.endpoint"] == "http://rustfs:9000"
        # Only the storage endpoint changes.
        assert body["other"] == "https://share.example.com" and body["metadata-location"] == "s3://b/m.json"
    finally:
        proxy.shutdown()
        upstream.shutdown()


def test_a_run_ends_well_before_its_token_expires(tmp_path):
    config = settings(tmp_path)
    # One-hour tokens (the platform default): RUN_TIMEOUT_SECONDS of 45 minutes fits.
    assert time_limit(config, {"expiresIn": 3600}) == 2700
    # The platform's longer tokens allow longer runs, up to RUN_TIMEOUT_SECONDS.
    longer = dataclasses.replace(config, run_timeout=6 * 3600)
    assert time_limit(longer, {"expiresIn": 7200}) == 7200 - 900
    assert time_limit(longer, {"expiresIn": 4 * 3600}) == 4 * 3600 - 900
    with pytest.raises(DbtError, match="POLARIS_TOKEN_LIFETIME"):
        time_limit(config, {"expiresIn": 1000})


class FakePolaris:
    """Polaris's semantic-model endpoints, enough for the runner: one store, versions counted."""

    def __init__(self):
        self.store, self.calls = {}, []

    def response(self, status, body=None, url=""):
        import requests

        response = requests.Response()
        response.status_code, response._content, response.url = status, json.dumps(body or {}).encode(), url
        return response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return self.response(200, self.store[url]) if url in self.store else self.response(404, url=url)

    def post(self, url, json, **kwargs):
        self.calls.append(("POST", url))
        self.store[f"{url}/{json['name']}"] = {"document": json["document"], "entity-version": 1}
        return self.response(200)

    def put(self, url, json, **kwargs):
        self.calls.append(("PUT", url))
        if json["entity-version"] != self.store[url]["entity-version"]:
            return self.response(409, url=url)
        self.store[url] = {"document": json["document"], "entity-version": json["entity-version"] + 1}
        return self.response(200)


MODEL_YAML = """\
version: "0.2.0"
semantic_model:
  - name: FlightPerformance
    description: Reliability of flights.
    datasets:
      - name: FLIGHT
        source: lakehouse.marts.fct_flights
        primary_key: [flight_id]
        fields:
          - name: flight_id
            expression: flight_id
    metrics:
      - name: flights
        description: "Scheduled flights. Unit: count."
        expression: count(*)
"""


def test_semantic_models_in_the_project_are_published_once_per_change(tmp_path):
    folder = tmp_path / "semantic_models" / "db1" / "marts"
    folder.mkdir(parents=True)
    (folder / "flight_performance.yml").write_text(MODEL_YAML)
    job = {"catalogs": {"db1": "db-1"}, "catalogUri": "http://polaris:8181/api/catalog"}
    polaris = FakePolaris()
    assert semantic.main(job, tmp_path, "token", polaris)["created"] == ["db1/marts/flight_performance.yml"]
    url = "http://polaris:8181/api/catalog/polaris/v1/db-1/namespaces/marts/semantic-models/flight_performance"
    stored = json.loads(polaris.store[url]["document"]["semantic_model"])
    assert stored["semantic_model"][0]["name"] == "FlightPerformance" and polaris.store[url]["entity-version"] == 1
    # An unchanged file keeps the stored version; an edit (here or elsewhere) is replaced by the file.
    assert semantic.main(job, tmp_path, "token", polaris)["unchanged"] == ["db1/marts/flight_performance.yml"]
    (folder / "flight_performance.yml").write_text(MODEL_YAML.replace("Reliability of flights.", "Punctuality."))
    assert semantic.main(job, tmp_path, "token", polaris)["updated"] == ["db1/marts/flight_performance.yml"]
    assert polaris.store[url]["entity-version"] == 2


def test_misplaced_or_broken_semantic_model_files_are_reported_not_published(tmp_path):
    root = tmp_path / "semantic_models"
    (root / "db1").mkdir(parents=True)
    (root / "other_db" / "marts").mkdir(parents=True)
    (root / "db1" / "no_namespace.yml").write_text(MODEL_YAML)
    (root / "other_db" / "marts" / "unknown_database.yml").write_text(MODEL_YAML)
    (root / "db1" / "marts").mkdir()
    (root / "db1" / "marts" / "not_a_model.yml").write_text("hello: [")
    (root / "db1" / "marts" / "empty.yml").write_text("name: x\n")
    polaris = FakePolaris()
    outcome = semantic.main({"catalogs": {"db1": "db-1"}, "catalogUri": "http://p/api/catalog"}, tmp_path, "t", polaris)
    errors = {e["model"]: e["error"] for e in outcome["errors"]}
    assert "namespace folder" in errors["db1/no_namespace.yml"]
    assert "not a database of this environment" in errors["other_db/marts/unknown_database.yml"]
    assert "does not parse" in errors["db1/marts/not_a_model.yml"]
    assert "not an Apache Ossie semantic model" in errors["db1/marts/empty.yml"]
    assert polaris.calls == [] and not outcome["created"]


def test_a_project_without_semantic_models_publishes_nothing(tmp_path):
    assert semantic.main({"catalogs": {}, "catalogUri": "http://p"}, tmp_path, "t", FakePolaris()) == {
        "created": [], "updated": [], "unchanged": [], "errors": []}


def test_semantic_models_take_missing_descriptions_from_the_dbt_project(tmp_path):
    write_info_schema(tmp_path / "target" / "info_schema" / "v1")
    docs = semantic.dbt_docs(tmp_path / "target")
    assert docs[("sales", ("staging",), "stg_regions")] == {"comment": "Cleaned regions",
                                                            "columns": {"region_id": "Stable id"}}
    folder = tmp_path / "semantic_models" / "sales" / "staging"
    folder.mkdir(parents=True)
    (folder / "regions.yml").write_text("""\
name: Regions
datasets:
  - name: REGION
    source: stg_regions
    fields:
      - {name: region_id, expression: {dialects: [{dialect: ANSI_SQL, expression: region_id}]}}
      - {name: code, expression: upper(region_id)}
""")
    polaris = FakePolaris()
    semantic.main({"catalogs": {"sales": "db-1"}, "catalogUri": "http://p/api/catalog"}, tmp_path, "t", polaris,
                  docs=docs)
    stored = json.loads(next(iter(polaris.store.values()))["document"]["semantic_model"])["semantic_model"][0]
    region = stored["datasets"][0]
    assert region["description"] == "Cleaned regions"
    assert [f.get("description") for f in region["fields"]] == ["Stable id", None]
