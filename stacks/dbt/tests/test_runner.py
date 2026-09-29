import json

import pytest

from dbt_portal import profiles
from dbt_portal.errors import DbtError
from dbt_portal.runs import forwarding
from runner import entrypoint, publish
from tests.conftest import write_info_schema


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
