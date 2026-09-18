"""Preserve user work and the physical invariants of the synthetic energy data."""

import pytest

from user_portal.notebook.seed import seed_workspace


def test_existing_workspace_receives_examples_without_overwriting_edits(tmp_path):
    personal = tmp_path / "workspace.py"
    personal.write_text("# Personal analysis")
    example = tmp_path / "01_pyiceberg_neighborhood.py"
    example.write_text("# Customized example")
    seed_workspace(tmp_path)
    assert personal.read_text() == "# Personal analysis"
    assert example.read_text() == "# Customized example"
    second = tmp_path / "02_duckdb_visualization.py"
    assert "mo.sql(" in second.read_text()
    assert "app = marimo.App" in second.read_text()
    second.write_text("# My SQL")
    seed_workspace(tmp_path)
    assert second.read_text() == "# My SQL"
    third = tmp_path / "03_duckdb_iceberg.py"
    assert "engine=lakehouse" in third.read_text()
    third.write_text("# My native analysis")
    seed_workspace(tmp_path)
    assert third.read_text() == "# My native analysis"
    for name in ("04_duckdb_iceberg_v3_write.py", "05_duckdb_iceberg_v3_read.py"):
        source = (tmp_path / name).read_text()
        assert "engine=lakehouse" in source
        # These examples run from top to bottom without input.
        assert "mo.ui." not in source and "mo.stop(" not in source
    assert "'format-version' = 3" in (tmp_path / "04_duckdb_iceberg_v3_write.py").read_text()


def test_sensor_events_are_reproducible_and_need_iceberg_v3_types():
    pytest.importorskip("pyarrow", reason="Run with --all-groups for notebook data tests")
    import json

    from user_portal.notebook.synthetic import generate_sensor_events

    data = generate_sensor_events()
    assert data.num_rows == 12 * 40 == 480
    assert data.equals(generate_sensor_events())
    assert str(data.schema.field("measured_at").type) == "timestamp[ns]"
    rows = data.to_pylist()
    assert [r["event_id"] for r in rows] == list(range(1, 481))
    assert all(r["synthetic"] and r["site"].startswith("Example") for r in rows)
    assert all(r["location_wkt"].startswith("POINT (") for r in rows)
    # Free-form payloads: several shapes, one of them nested.
    shapes = {tuple(sorted(json.loads(r["payload"]))) for r in rows}
    assert len(shapes) == 4 and ("code", "details", "kind") in shapes
    # Nanoseconds identify events that microseconds would merge.
    nanoseconds = {(r["device_id"], r["measured_at"].value) for r in rows}
    microseconds = {(r["device_id"], r["measured_at"].value // 1_000) for r in rows}
    assert len(microseconds) < len(nanoseconds) == len(rows)


def test_energy_data_is_reproducible_and_balanced():
    pytest.importorskip("pyarrow", reason="Run with --all-groups for notebook data tests")
    from user_portal.notebook.synthetic import generate_energy_data

    data = generate_energy_data()
    assert data.num_rows == 40 * 7 * 96 == 26880
    assert data.equals(generate_energy_data())
    assert str(data.schema.field("timestamp_utc").type) == "timestamp[us, tz=UTC]"
    rows = data.to_pylist()
    assert len({(r["home_id"], r["timestamp_utc"]) for r in rows}) == len(rows)
    assert len({r["street"] for r in rows}) == 4
    assert all(r["synthetic"] and r["postal_code"] == "0000 ZZ" for r in rows)
    assert all(
        abs(r["consumption_kwh"] - r["generation_kwh"] - r["grid_draw_kwh"] + r["grid_export_kwh"]) < 1e-8
        for r in rows
    )
    assert all(r["grid_draw_kwh"] >= 0 and r["grid_export_kwh"] >= 0 for r in rows)


def test_duckdb_notebook_executes_sql_and_reacts_to_street_choice(monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("pyarrow")
    pytest.importorskip("sqlglot")
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from user_portal.notebook.synthetic import generate_energy_data

    monkeypatch.setenv("ICEBERG_DATABASE", "test_database")
    path = Path(__file__).parents[1] / "user_portal/notebook/examples/02_duckdb_visualization.py"
    spec = importlib.util.spec_from_file_location("energy_analysis", path)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    frame = generate_energy_data().to_pandas()
    frame = frame.assign(
        local_timestamp=frame["timestamp_utc"].dt.tz_convert("Europe/Amsterdam").dt.tz_localize(None)
    )
    for choice in ("All streets", "Example Solar Street"):
        _, definitions = notebook.app.run(
            defs={"energy_frame": frame, "street_choice": SimpleNamespace(value=choice)}
        )
        hourly = definitions["hourly_energy"]
        streets = definitions["street_totals"]
        expected = frame if choice == "All streets" else frame.loc[frame["street"] == choice]
        assert len(hourly) == 7 * 24
        assert len(streets) == 4
        assert streets["homes"].sum() == 40
        assert hourly["consumption_kwh"].sum() == pytest.approx(expected["consumption_kwh"].sum())
        assert hourly["generation_kwh"].sum() == pytest.approx(expected["generation_kwh"].sum())
