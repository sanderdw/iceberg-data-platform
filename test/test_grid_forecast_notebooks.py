"""Exercise the grid forecast product: Polars generation, contract SQL, Iceberg round trip and both notebooks."""

import copy
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

pl = pytest.importorskip("polars")
duckdb = pytest.importorskip("duckdb")
pytest.importorskip("yaml")

from test.test_flights_notebooks import polaris, run_notebook
from user_portal.notebook.connection import polars_storage_options
from user_portal.notebook.duckdb_connection import EXAMPLE_PROPERTY
from user_portal.notebook.grid_forecast import (
    EXAMPLE,
    collect_product,
    generate_grid_forecast,
    load_contract,
    polars_schema,
    publish_grid_forecast,
    quality_report,
    require_quality,
    semantic_model,
)
from user_portal.notebook.seed import seed_workspace
from user_portal.notebook.semantic import joins, metric_sql, model_check_queries

EXAMPLES = Path(__file__).parents[1] / "user_portal/notebook/examples"
NAMESPACE = ["ai_grid_forecast"]
ROWS = {"SUBSTATION": 6, "FEEDER": 48, "ASSET": 288, "LOAD_FORECAST": 8640, "SPREADING_MODEL": 5,
        "SPREADING_CONTRIBUTION": 21600, "LARGE_CUSTOMER": 49, "CONGESTION": 576}


@pytest.fixture(scope="module")
def contract():
    return load_contract(EXAMPLES)


@pytest.fixture(scope="module")
def product(contract):
    return collect_product(generate_grid_forecast(contract))


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from pyiceberg.catalog.sql import SqlCatalog

    config = {"type": "sql", "uri": f"sqlite:///{tmp_path}/catalog.db", "warehouse": f"file://{tmp_path}/warehouse"}
    monkeypatch.setenv("ICEBERG_DATABASE", "db-test")
    monkeypatch.delenv("ICEBERG_S3_ENDPOINT", raising=False)
    monkeypatch.setattr("user_portal.notebook.connection.connect", lambda: SqlCatalog("default", **config))
    return SqlCatalog("default", **config)


def test_generated_product_follows_the_contract_and_is_reproducible(contract, product):
    require_quality(quality_report(product, contract))
    assert {name: table.height for name, table in product.items()} == ROWS
    for name, table in product.items():
        assert table.schema == pl.Schema(polars_schema(contract["datasets"][name]))
    again = collect_product(generate_grid_forecast(contract))
    assert all(again[name].equals(table) for name, table in product.items())
    congestion = product["CONGESTION"]
    assert congestion["first_congestion_year"].is_not_null().sum() == 162
    assert congestion["first_congestion_year"].min() == 2029
    assert set(congestion["severity"]) == {"none", "moderate", "high", "critical"}


def corrupt(table, column, value, row=0):
    return table.with_columns(pl.when(pl.int_range(pl.len()) == row).then(pl.lit(value)).otherwise(pl.col(column))
                              .cast(table.schema[column]).alias(column))


@pytest.mark.parametrize(("dataset", "change"), [
    ("ASSET", lambda t: corrupt(t, "asset_id", "AS-ALD-01-C2")),
    ("ASSET", lambda t: corrupt(t, "feeder_id", "FD-ZZZ-01")),
    ("FEEDER", lambda t: corrupt(t, "voltage_kv", 50)),
    ("SUBSTATION", lambda t: corrupt(t, "latitude", 91)),
    ("LOAD_FORECAST", lambda t: t.with_columns(pl.col("peak_mw") + pl.when(pl.int_range(pl.len()) == 7).then(1).otherwise(0))),
    ("LOAD_FORECAST", lambda t: t.filter(pl.int_range(pl.len()) != 3)),
    ("SPREADING_CONTRIBUTION", lambda t: corrupt(t, "contribution_mw", 9.9, row=11)),
    ("LARGE_CUSTOMER", lambda t: corrupt(t, "planned_year", 2030)),
    ("CONGESTION", lambda t: corrupt(t, "severity", "critical")),
    ("CONGESTION", lambda t: t.with_columns(pl.col("first_congestion_year").fill_null(2040))),
    ("SPREADING_MODEL", lambda t: corrupt(t, "synthetic", False)),
])
def test_quality_contract_detects_broken_rows(contract, product, dataset, change):
    broken = {**product, dataset: change(product[dataset])}
    with pytest.raises(ValueError, match="quality checks failed"):
        require_quality(quality_report(broken, contract))


def test_contract_sql_and_stored_metrics_give_the_same_answer_in_polars_and_duckdb(contract, product):
    semantic = semantic_model(contract, NAMESPACE)
    polars_sql = pl.SQLContext(product, eager=True)
    with duckdb.connect() as connection:
        for name, table in product.items():
            connection.register(name, table.to_arrow())
        for check in contract["quality_checks"]:
            assert polars_sql.execute(check["sql"]).item() == connection.execute(check["sql"]).fetchone()[0] == 0
        for label, query in model_check_queries(semantic):
            assert polars_sql.execute(query).item() == connection.execute(query).fetchone()[0] == 0, label
        for metric in semantic["metrics"]:
            home = re.search(r"\b([A-Z_]+)\.", metric_sql(semantic, metric["name"])).group(1)
            path = {"LOAD_FORECAST": ["forecast_asset"], "CONGESTION": ["congestion_asset"],
                    "LARGE_CUSTOMER": ["customer_asset"],
                    "SPREADING_CONTRIBUTION": ["contribution_forecast", "forecast_asset"]}[home]
            query = (f"SELECT SUBSTATION.substation_id AS substation, {metric_sql(semantic, metric['name'])} AS value "
                     f"FROM {home} {joins(semantic, *path, 'asset_feeder', 'feeder_substation')} "
                     "GROUP BY SUBSTATION.substation_id ORDER BY substation")
            in_polars = polars_sql.execute(query).rows()
            in_duckdb = connection.execute(query).fetchall()
            assert [s for s, _ in in_polars] == [s for s, _ in in_duckdb]
            assert [v for _, v in in_polars] == pytest.approx([v for _, v in in_duckdb]), metric["name"]


def test_semantic_model_is_complete_and_passes_the_portal_rules(contract):
    pytest.importorskip("sqlglot")
    from user_portal.semantic.rules import validate

    semantic = semantic_model(contract, NAMESPACE)
    stored, warnings = validate(semantic)
    assert warnings == [] and stored == semantic
    assert json.loads(json.dumps(semantic)) == semantic
    assert [d["source"] for d in semantic["datasets"]] == [
        "lakehouse.ai_grid_forecast." + d["table"] for d in contract["datasets"].values()]
    fields = {(d["name"], f["name"]): f for d in semantic["datasets"] for f in d["fields"]}
    assert all(f.get("description", "").endswith(".") for f in fields.values())
    assert {f["label"] for f in fields.values() if "label" in f} <= set(contract["concepts"])
    times = [key for key, f in fields.items() if f.get("dimension", {}).get("is_time")]
    assert times == [("LOAD_FORECAST", "year"), ("SPREADING_CONTRIBUTION", "year")]
    assert fields[("CONGESTION", "severity")]["ai_context"]["examples"] == ["none", "moderate", "high", "critical"]
    assert fields[("LOAD_FORECAST", "utilization_pct")]["datatype"] == "Float"
    instructions = semantic["ai_context"]["instructions"]
    assert "Never add peak_mw across assets" in instructions and "Grain:" in instructions


@pytest.mark.parametrize(("change", "message"), [
    (lambda c: c["datasets"]["ASSET"]["fields"]["rated_mw"].update(label="Rating"), "ASSET.rated_mw"),
    (lambda c: c["relationships"][0].update(from_columns=["station"]), "feeder_substation"),
    (lambda c: c["metrics"]["congestion_points"].update(sql="count(CONGESTION.year)"), "congestion_points"),
    (lambda c: c["datasets"]["LOAD_FORECAST"].update(partition_by=["month"]), "month"),
])
def test_semantic_model_rejects_a_contract_that_contradicts_itself(contract, change, message):
    broken = copy.deepcopy(contract)
    change(broken)
    with pytest.raises(ValueError, match=message):
        semantic_model(broken, NAMESPACE)


def test_governed_queries_count_congestion_points_per_substation(contract, product):
    pytest.importorskip("sqlglot")
    pytest.importorskip("pydantic")
    from user_portal.semantic import ossie
    from user_portal.semantic.compiler import QuerySpec, compile_query
    from user_portal.semantic.rules import document

    semantic = semantic_model(contract, NAMESPACE)
    parsed = ossie.parse({"document": document(semantic), "entity-version": 1}, NAMESPACE)
    types = {"String": "string", "Integer": "int", "Float": "double", "Boolean": "boolean"}
    schemas = {d["name"]: {f["name"]: types[f["datatype"]] for f in d["fields"]} for d in semantic["datasets"]}
    spec = QuerySpec(metrics=["congestion_points"], dimensions=[{"field": "SUBSTATION.substation_id"}])
    compiled = compile_query(parsed, schemas, spec)
    with duckdb.connect() as connection:
        for binding in compiled.bindings:
            connection.register(binding["view"], product[binding["view"]].to_arrow())
        rows = dict(connection.execute(compiled.sql, compiled.params).fetchall())
    expected = dict(pl.SQLContext(product, eager=True).execute(
        "SELECT s.substation_id, count(*) FILTER (WHERE c.first_congestion_year IS NOT NULL) FROM CONGESTION c "
        "JOIN ASSET a ON c.asset_id = a.asset_id JOIN FEEDER f ON a.feeder_id = f.feeder_id "
        "JOIN SUBSTATION s ON f.substation_id = s.substation_id GROUP BY s.substation_id").rows())
    assert rows == expected and sum(rows.values()) == 162


def test_publication_writes_documented_partitioned_tables_and_keeps_them_on_rerun(contract, product, catalog):
    report = quality_report(product, contract)
    first = publish_grid_forecast(catalog, dict(product), contract, NAMESPACE, report)
    assert {row["dataset"]: row["rows"] for row in first} == ROWS
    uuids = {d["table"]: catalog.load_table((*NAMESPACE, d["table"])).metadata.table_uuid
             for d in contract["datasets"].values()}
    forecast = catalog.load_table((*NAMESPACE, "load_forecast"))
    assert forecast.properties[EXAMPLE_PROPERTY] == EXAMPLE
    assert forecast.properties["comment"].startswith("Expected yearly peak per asset")
    assert forecast.properties["iceberg-data-platform.producer"] == "notebook"
    assert forecast.properties["iceberg-data-platform.quality.summary"] == "10 of 10 contract checks passed"
    assert [f.name for f in forecast.spec().fields] == ["year"]
    assert forecast.schema().find_field("utilization_pct").doc.startswith("peak_mw as a percentage")
    congestion = catalog.load_table((*NAMESPACE, "congestion"))
    assert [i["name"] for i in json.loads(congestion.properties["iceberg-data-platform.lineage.inputs"])] == [
        "load_forecast", "asset"]
    second = publish_grid_forecast(catalog, dict(product), contract, NAMESPACE, report)
    assert [row["rows"] for row in second] == [row["rows"] for row in first]
    assert uuids == {d["table"]: catalog.load_table((*NAMESPACE, d["table"])).metadata.table_uuid
                     for d in contract["datasets"].values()}
    stored = pl.scan_iceberg(catalog.load_table((*NAMESPACE, "load_forecast"))).collect()
    assert stored.sort("asset_id", "year", "direction").equals(product["LOAD_FORECAST"])


def test_a_changed_contract_recreates_only_that_table(contract, product, catalog):
    report = quality_report(product, contract)
    publish_grid_forecast(catalog, dict(product), contract, NAMESPACE, report)
    before = {d["table"]: catalog.load_table((*NAMESPACE, d["table"])).metadata.table_uuid
              for d in contract["datasets"].values()}
    changed = copy.deepcopy(contract)
    changed["datasets"]["SUBSTATION"]["fields"]["name"]["description"] = "The substation's name."
    changed["datasets"]["CONGESTION"].pop("derived_from")
    del changed["datasets"]["FEEDER"]["fields"]["name"]
    tables = {**product, "FEEDER": product["FEEDER"].drop("name")}
    publish_grid_forecast(catalog, tables, changed, NAMESPACE, report)
    after = {d["table"]: catalog.load_table((*NAMESPACE, d["table"])).metadata.table_uuid
             for d in contract["datasets"].values()}
    assert {table for table in before if before[table] != after[table]} == {"feeder"}
    substation = catalog.load_table((*NAMESPACE, "substation"))
    assert substation.schema().find_field("name").doc == "The substation's name."


def test_publication_preflights_all_tables_before_writing_any(contract, product):
    catalog = Mock()
    catalog.table_exists.side_effect = lambda identifier: identifier[-1] == "congestion"
    catalog.load_table.return_value.properties = {EXAMPLE_PROPERTY: "someone_else"}
    with pytest.raises(RuntimeError, match="congestion already exists and is not owned"):
        publish_grid_forecast(catalog, dict(product), contract, NAMESPACE, quality_report(product, contract))
    catalog.create_namespace_if_not_exists.assert_not_called()
    catalog.create_table.assert_not_called()
    catalog.drop_table.assert_not_called()


def test_publication_refuses_failed_quality(contract, product):
    catalog = Mock()
    with pytest.raises(ValueError, match="quality checks failed"):
        publish_grid_forecast(catalog, dict(product), contract, NAMESPACE, [{"check": "x", "violations": 1}])
    assert catalog.mock_calls == []


def test_polars_gets_only_the_storage_settings(monkeypatch):
    table = Mock()
    table.io.properties = {"comment": "x", "uri": "sqlite:///c.db", "s3.endpoint": "http://localhost:9000",
                           "s3.access-key-id": "AK", "s3.secret-access-key": "SK", "s3.session-token": "ST"}
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")
    assert polars_storage_options(table) == {
        "s3.endpoint": "http://rustfs:9000", "s3.access-key-id": "AK", "s3.secret-access-key": "SK",
        "s3.session-token": "ST", "s3.region": "us-east-1", "allow_http": "true"}
    monkeypatch.delenv("ICEBERG_S3_ENDPOINT")
    table.io.properties = {"comment": "x", "warehouse": "file:///tmp"}
    assert polars_storage_options(table) == {}


def test_workspace_gets_the_contract_and_keeps_user_edits(tmp_path):
    seed_workspace(tmp_path)
    contract_file = tmp_path / "grid_forecast.product.yaml"
    assert contract_file.read_bytes() == (EXAMPLES / "grid_forecast.product.yaml").read_bytes()
    contract_file.write_text("# User changes")
    seed_workspace(tmp_path)
    assert contract_file.read_text() == "# User changes"


def test_both_notebooks_execute_against_a_local_catalog(tmp_path, monkeypatch, catalog):
    pytest.importorskip("marimo")
    pytest.importorskip("matplotlib")
    workspace = tmp_path / "work"
    workspace.mkdir()
    seed_workspace(workspace)
    store = polaris(monkeypatch)
    written = run_notebook(workspace / "09_polars_grid_forecast_write.py")
    assert {row["dataset"]: row["rows"] for row in written["published"]} == ROWS
    assert written["model_action"] == "created" and list(store.models) == ["mv_load_forecast"]
    document, _ = store.models["mv_load_forecast"]
    assert json.loads(document["semantic_model"])["semantic_model"] == [written["grid_model"]]
    assert written["congestion_overview"]["congestion_points"].sum() == 162
    rerun = run_notebook(workspace / "09_polars_grid_forecast_write.py")
    assert rerun["model_action"] == "updated"
    # The reader gets everything from Polaris: remove the local file it must not need.
    (workspace / "grid_forecast.product.yaml").unlink()
    read = run_notebook(workspace / "10_polars_grid_forecast_read.py")
    assert read["grid_model"] == written["grid_model"] and read["model_version"] == "2"
    assert len(read["checks"]) == 15 and all(c["violations"] == 0 for c in read["checks"])
    assert 'JOIN "SUBSTATION" ON "FEEDER"."substation_id" = "SUBSTATION"."substation_id"' in read["overview_sql"]
    assert read["congestion_overview"]["congestion_points"].sum() == 162
    assert read["engines_agree"]["agree"].all() and read["engines_agree"].height == 30
    pitfall = read["peak_sum_pitfall"]
    assert (pitfall["added_up_peaks_mw"] > pitfall["largest_rating_mw"]).all()
    assert read["worst_assets"].height == 10 and read["area_customers"]["status"].unique().sort().to_list() == [
        "connected", "future"]


def test_reader_stops_without_a_published_model(tmp_path, monkeypatch, catalog):
    pytest.importorskip("marimo")
    seed_workspace(tmp_path)
    polaris(monkeypatch)
    with pytest.raises(RuntimeError, match="Run notebook 09 first"):
        run_notebook(tmp_path / "10_polars_grid_forecast_read.py")
