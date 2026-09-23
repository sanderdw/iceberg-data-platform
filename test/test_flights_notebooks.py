"""Exercise the data product's semantics, SQL and notebook dependency graph."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("yaml")

from user_portal.notebook.flights import (
    datasets,
    generate_flights,
    load_semantics,
    publish_flights,
    quality_report,
    require_quality,
)
from user_portal.notebook.seed import seed_workspace

EXAMPLES = Path(__file__).parents[1] / "user_portal/notebook/examples"


@pytest.fixture
def flights():
    model, contract, fingerprints = load_semantics(EXAMPLES)
    with duckdb.connect() as connection:
        generate_flights(connection, model)
        yield connection, model, contract, fingerprints


def test_generated_product_matches_all_mapped_fields_and_is_reproducible(flights):
    connection, model, contract, _ = flights
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
    connection, _, contract, _ = flights
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


def test_both_notebooks_execute_and_explain_metrics(tmp_path, monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("matplotlib")
    pytest.importorskip("sqlglot")
    seed_workspace(tmp_path)
    with duckdb.connect() as connection:
        # The integration smoke covers real Iceberg publication. Here run every
        # generation/quality/analysis cell on DuckDB without a live catalog.
        def publication(conn, model, namespace, fingerprints):
            assert conn is connection
            assert namespace == ["ai_flights"] and len(fingerprints["ontology_sha256"]) == 64
            return [
                {"dataset": d["name"], "rows": conn.execute(f"SELECT count(*) FROM {d['name']}").fetchone()[0]}
                for d in datasets(model)
            ]

        monkeypatch.setattr("user_portal.notebook.flights.publish_flights", publication)
        written = run_notebook(tmp_path / "06_duckdb_flights_write.py", lakehouse=connection)
        assert next(r["rows"] for r in written["published"] if r["dataset"] == "FLIGHT") == 12000
        read = run_notebook(tmp_path / "07_duckdb_flights_read.py", lakehouse=connection)
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
    model, _, fingerprints = load_semantics(EXAMPLES)
    connection = Mock()
    # A foreign table late in the dataset list must not cause earlier tables to be dropped.
    connection.execute.return_value.fetchone.side_effect = [(0,)] * 5 + [(1,), ("someone_else",)]
    with pytest.raises(RuntimeError, match="not owned"):
        publish_flights(connection, model, ["ai_flights"], fingerprints)
    assert all(call.args[0].startswith("SELECT") for call in connection.execute.call_args_list)
