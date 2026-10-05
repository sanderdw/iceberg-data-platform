"""Exercise the data product's semantics, SQL and notebook dependency graph."""

import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("yaml")

from test.test_semantic_client import BASE, Store
from user_portal.notebook.flights import (
    datasets,
    generate_flights,
    load_semantics,
    publish_flights,
    quality_report,
    require_quality,
    semantic_model,
)
from user_portal.notebook.seed import seed_workspace
from user_portal.notebook.semantic import SPEC_VERSION, SemanticModels, model_checks

EXAMPLES = Path(__file__).parents[1] / "user_portal/notebook/examples"


@pytest.fixture
def flights():
    model, contract = load_semantics(EXAMPLES)
    with duckdb.connect() as connection:
        generate_flights(connection, model)
        yield connection, model, contract


def test_generated_product_matches_all_mapped_fields_and_is_reproducible(flights):
    connection, model, contract = flights
    require_quality(quality_report(connection, contract))
    assert connection.execute("SELECT count(*) FROM FLIGHT").fetchone() == (12000,)
    assert connection.execute("SELECT count(*) FROM ROUTE").fetchone() == (56,)
    assert connection.execute("SELECT count(*) FROM AIRCRAFT").fetchone() == (120,)
    for dataset in datasets(model):
        relation = connection.execute(f"SELECT * FROM {dataset['name']} ORDER BY ALL")
        assert [c[0] for c in relation.description] == [f["name"] for f in dataset["fields"]] + ["synthetic"]
    before = connection.execute("SELECT * FROM FLIGHT ORDER BY id").fetchall()
    generate_flights(connection, model)
    assert connection.execute("SELECT * FROM FLIGHT ORDER BY id").fetchall() == before
    generate_flights(connection, model, 10000)
    require_quality(quality_report(connection, contract))
    with pytest.raises(ValueError, match="10,000"):
        generate_flights(connection, model, 9999)


@pytest.mark.parametrize("corruption", [
    "UPDATE generated_flights SET route_id = 'missing' WHERE id = 'DEMO-000002'",
    "UPDATE generated_flights SET carrier_code = 'XB' WHERE id = 'DEMO-000001'",
    "UPDATE generated_flights SET dep_delay = 0 WHERE cancelled",
    "UPDATE generated_flights SET arr_delay = 999 WHERE id = 'DEMO-000002'",
    "UPDATE generated_flights SET id = 'duplicate' WHERE NOT cancelled",
    "UPDATE generated_airports SET latitude = 91 WHERE code = 'QAA'",
    "UPDATE generated_routes SET dest_airport_code = orig_airport_code",
    "UPDATE generated_flights SET cancel_code = 'Z' WHERE cancelled",
])
def test_quality_contract_detects_broken_semantics(flights, corruption):
    connection, _, contract = flights
    connection.execute(corruption)
    with pytest.raises(ValueError, match="quality checks failed"):
        require_quality(quality_report(connection, contract))


def test_workspace_gets_semantics_and_preserves_user_edits(tmp_path):
    seed_workspace(tmp_path)
    for name in ("flights.yaml", "flights.product.yaml", "OSSIE-NOTICE.txt"):
        assert (tmp_path / name).read_bytes() == (EXAMPLES / name).read_bytes()
        (tmp_path / name).write_text("# User changes")
    seed_workspace(tmp_path)
    assert (tmp_path / "flights.product.yaml").read_text() == "# User changes"


def run_notebook(path, **definitions):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    return notebook.app.run(defs=definitions)[1]


def polaris(monkeypatch):
    """Semantic models kept by a stand-in for Polaris, shared by every connection in the test."""
    store = Store()
    monkeypatch.setattr(
        SemanticModels,
        "connect",
        classmethod(lambda cls, namespace, version=SPEC_VERSION: cls(
            httpx.Client(transport=httpx.MockTransport(store)), BASE, version
        )),
    )
    return store


def test_both_notebooks_execute_and_explain_metrics(tmp_path, monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("matplotlib")
    pytest.importorskip("sqlglot")
    seed_workspace(tmp_path)
    store = polaris(monkeypatch)
    with duckdb.connect() as connection:
        # The integration smoke covers real Iceberg publication. Here run every
        # generation/quality/analysis cell on DuckDB without a live catalog.
        def publication(conn, model, namespace):
            assert conn is connection and namespace == ["ai_flights"]
            return [
                {"dataset": d["name"], "rows": conn.execute(f"SELECT count(*) FROM {d['name']}").fetchone()[0]}
                for d in datasets(model)
            ]

        monkeypatch.setattr("user_portal.notebook.flights.publish_flights", publication)
        written = run_notebook(tmp_path / "06_duckdb_flights_write.py", lakehouse=connection)
        assert next(r["rows"] for r in written["published"] if r["dataset"] == "FLIGHT") == 12000
        assert written["model_action"] == "created" and list(store.models) == ["flights"]
        document, _ = store.models["flights"]
        assert document["version"] == "0.2.0.dev0"
        assert json.loads(document["semantic_model"])["semantic_model"] == [written["flights_model"]]
        assert run_notebook(tmp_path / "06_duckdb_flights_write.py", lakehouse=connection)["model_action"] == "updated"
        # The reader gets everything from Polaris: remove the local files it must not need.
        for name in ("flights.yaml", "flights.product.yaml"):
            (tmp_path / name).unlink()
        read = run_notebook(tmp_path / "07_duckdb_flights_read.py", lakehouse=connection)
        assert read["flights_model"] == written["flights_model"] and read["model_version"] == "2"
        assert all(c["violations"] == 0 for c in read["checks"]) and len(read["checks"]) == 13
        assert 'JOIN "AIRPORT" ON "ROUTE"."orig_airport_code" = "AIRPORT"."code"' in read["airport_delay_sql"]
        overview = read["overview"].iloc[0]
        assert overview["scheduled_flights"] == 12000
        assert overview["canceled_flights"] == 293
        assert overview["diverted_flights"] == 120
        assert read["airport_delays"]["scheduled_flights"].sum() == 12000
        assert read["carrier_performance"]["eligible_arrivals"].sum() == 12000 - 293 - 120
        cardinality = read["join_cardinality"].iloc[0]
        assert cardinality["flight_instances"] == cardinality["correct_join_rows"] == 12000
        assert cardinality["runway_join_rows"] == 24000
        comparison = read["denominator_comparison"].iloc[0]
        assert comparison["on_time_share_of_all_scheduled_pct"] < comparison["on_time_share_of_eligible_arrivals_pct"]
        assert comparison["misleading_zero_filled_delay_minutes"] < comparison["observed_departure_delay_minutes"]


def test_publication_preflights_all_tables_before_replacing_any():
    model, _ = load_semantics(EXAMPLES)
    connection = Mock()
    # A foreign table late in the dataset list must not cause earlier tables to be dropped.
    connection.execute.return_value.fetchone.side_effect = [(0,)] * 5 + [(1,), ("someone_else",)]
    with pytest.raises(RuntimeError, match="not owned"):
        publish_flights(connection, model, ["ai_flights"])
    assert all(call.args[0].startswith("SELECT") for call in connection.execute.call_args_list)


def test_semantic_model_describes_the_six_published_tables(flights):
    _, model, contract = flights
    semantic = semantic_model(model, contract, ["ai_flights"])
    assert [d["source"] for d in semantic["datasets"]] == [
        "lakehouse.ai_flights." + table for table in ("runways", "aircraft", "airports", "flights", "carriers", "routes")
    ]
    assert all(d["primary_key"] and d["fields"] for d in semantic["datasets"])
    fields = {d["name"]: {f["name"] for f in d["fields"]} for d in semantic["datasets"]}
    for relationship in semantic["relationships"]:
        assert set(relationship["from_columns"]) <= fields[relationship["from"]]
        assert set(relationship["to_columns"]) <= fields[relationship["to"]]
    metrics = {m["name"]: m["expression"]["dialects"][0]["expression"] for m in semantic["metrics"]}
    assert set(metrics) == set(contract["metrics"])
    assert "f." not in metrics["cancellation_pct"] and "FLIGHT.cancelled" in metrics["cancellation_pct"]
    assert json.loads(json.dumps(semantic)) == semantic
    assert isinstance(semantic["ai_context"]["instructions"], str)
    # Ossie dimensions: the date is a time dimension, carriers group by name, measures stay unflagged.
    flagged = {(d["name"], f["name"]): f["dimension"] for d in semantic["datasets"] for f in d["fields"] if "dimension" in f}
    assert flagged[("FLIGHT", "date")] == {"is_time": True} and flagged[("CARRIER", "name")] == {"is_time": False}
    assert ("FLIGHT", "dep_delay") not in flagged


def test_stored_metrics_give_the_same_answer_as_notebook_seven(flights):
    connection, model, contract = flights
    semantic = semantic_model(model, contract, ["ai_flights"])
    expected = connection.execute(f"SELECT round({contract['metrics']['cancellation_pct']['sql']}, 3) FROM FLIGHT f").fetchone()
    stored = next(m for m in semantic["metrics"] if m["name"] == "cancellation_pct")["expression"]["dialects"][0]["expression"]
    assert connection.execute(f"SELECT round({stored}, 3) FROM FLIGHT").fetchone() == expected


def test_semantic_model_rejects_a_mapping_without_the_join_columns(flights):
    _, model, contract = flights
    broken = copy.deepcopy(model)
    next(d for d in datasets(broken) if d["name"] == "ROUTE")["fields"].pop(0)
    with pytest.raises(ValueError, match="not mapped|no field"):
        semantic_model(broken, contract, ["ai_flights"])


@pytest.mark.parametrize(
    ("corruption", "failed"),
    [
        ("UPDATE generated_flights SET id = 'DEMO-000001' WHERE id = 'DEMO-000002'", "FLIGHT key (id) is unique"),
        ("UPDATE generated_flights SET route_id = 'missing' WHERE id = 'DEMO-000002'", "flight_route"),
        ("UPDATE generated_routes SET orig_airport_code = 'ZZZ' WHERE id = (SELECT min(id) FROM generated_routes)",
         "route_departure_airport"),
    ],
)
def test_checks_from_the_stored_model_find_broken_keys_and_joins(flights, corruption, failed):
    connection, model, contract = flights
    semantic = semantic_model(model, contract, ["ai_flights"])
    assert all(c["violations"] == 0 for c in model_checks(connection, semantic))
    connection.execute(corruption)
    assert next(c["check"] for c in model_checks(connection, semantic) if c["violations"]).startswith(failed)


def test_reader_stops_without_a_published_model(tmp_path, monkeypatch):
    pytest.importorskip("marimo")
    seed_workspace(tmp_path)
    polaris(monkeypatch)
    with duckdb.connect() as connection, pytest.raises(RuntimeError, match="Run notebook 06 first"):
        run_notebook(tmp_path / "07_duckdb_flights_read.py", lakehouse=connection)
