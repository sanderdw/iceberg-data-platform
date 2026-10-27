"""Preserve user work and the physical invariants of the synthetic energy data."""

import pytest

from user_portal.notebook.seed import seed_workspace


def test_existing_workspace_receives_examples_without_overwriting_edits(tmp_path):
    personal = tmp_path / "workspace.py"
    personal.write_text("# Personal analysis")
    example = tmp_path / "01_pyiceberg_neighborhood.py"
    example.write_text("# Customized example")
    seed_workspace(tmp_path)
    assert "shared_objects" in (tmp_path / "shared_workspace.py").read_text()
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
    explained = (tmp_path / "11_duckdb_iceberg_explained.py").read_text()
    # Replaces only its own table on a rerun, and runs from top to bottom without input.
    assert "drop_example_table(" in explained and "EXAMPLE_PROPERTY" in explained
    assert "mo.ui." not in explained and "mo.stop(" not in explained


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
    # Sizes too small for a microsecond collision are still valid.
    assert generate_sensor_events(devices=1, events=1).num_rows == 1
    assert generate_sensor_events(devices=0).num_rows == 0


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


def test_duckdb_notebook_executes_sql_with_plain_street_setting(monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("pyarrow")
    pytest.importorskip("sqlglot")
    import importlib.util
    from pathlib import Path

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
            defs={"energy_frame": frame, "STREET": choice}
        )
        hourly = definitions["hourly_energy"]
        streets = definitions["street_totals"]
        expected = frame if choice == "All streets" else frame.loc[frame["street"] == choice]
        assert len(hourly) == 7 * 24
        assert len(streets) == 4
        assert streets["homes"].sum() == 40
        assert hourly["consumption_kwh"].sum() == pytest.approx(expected["consumption_kwh"].sum())
        assert hourly["generation_kwh"].sum() == pytest.approx(expected["generation_kwh"].sum())


def test_plain_writer_runs_without_a_button_and_skips_existing_rows(monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("pyarrow")
    import importlib.util
    from pathlib import Path
    from unittest.mock import Mock

    from user_portal.notebook.synthetic import generate_energy_data

    monkeypatch.setenv("ICEBERG_DATABASE", "test_database")
    data = generate_energy_data()
    table = Mock()
    table.scan.return_value.count.side_effect = [0, data.num_rows]
    table.scan.return_value.to_arrow.return_value = data.slice(0, 10)
    catalog = Mock()
    catalog.create_table_if_not_exists.return_value = table
    catalog.load_table.return_value = table
    monkeypatch.setattr("user_portal.notebook.connection.connect", lambda: catalog)
    path = Path(__file__).parents[1] / "user_portal/notebook/examples/01_pyiceberg_neighborhood.py"
    spec = importlib.util.spec_from_file_location("plain_energy_writer", path)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    first = notebook.app.run()[1]
    second = notebook.app.run()[1]
    assert first["write_message"] == "Created and populated: 26,880 rows."
    assert second["write_message"] == "Table already contains 26,880 rows; append skipped."
    table.append.assert_called_once()


def test_catalog_properties_sign_in_as_the_notebook_owner(monkeypatch):
    pytest.importorskip("pyiceberg")
    from user_portal.notebook import connection

    for name, value in {
        "ICEBERG_DATABASE": "db-1", "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
        "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
        "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ICEBERG_SESSION_TOKEN_URL", raising=False)
    monkeypatch.setenv("ICEBERG_ACCESS_TOKEN", "owner-token")
    properties = connection.catalog_properties()
    assert properties["type"] == "rest" and properties["warehouse"] == "db-1"
    assert properties["token"] == "owner-token"
    assert properties["header.X-Iceberg-Access-Delegation"] == "vended-credentials"
    assert properties["py-io-impl"] == "user_portal.notebook.connection.InternalS3FileIO"

    monkeypatch.setenv("ICEBERG_SESSION_TOKEN_URL", "http://workspace-gateway:3002/internal/notebooks/n/token")
    monkeypatch.setattr(connection, "access_token", lambda: "fresh")
    properties = connection.catalog_properties()
    assert "token" not in properties
    assert properties["auth"] == {"type": "custom", "impl": "user_portal.notebook.connection.SessionAuthManager"}


def test_session_catalog_signs_requests_and_keeps_plain_string_properties(monkeypatch):
    pytest.importorskip("pyiceberg")
    import requests
    from pyiceberg.catalog.rest import RestCatalog

    from user_portal.notebook import connection

    for name, value in {
        "ICEBERG_DATABASE": "db-1", "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
        "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
        "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
        "ICEBERG_SESSION_TOKEN_URL": "http://workspace-gateway:3002/internal/notebooks/n/token",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(connection, "access_token", lambda: "fresh")
    monkeypatch.setattr(RestCatalog, "_fetch_config", lambda self: None)
    catalog = connection.connect()
    # Polars' Iceberg sink copies these and refuses anything but strings, such as a nested auth dict.
    assert isinstance(catalog, connection.SessionRestCatalog)
    assert all(isinstance(value, str) for value in catalog.properties.values())
    request = catalog._session.prepare_request(requests.Request("GET", "http://polaris:8181/api/catalog/v1/config"))
    assert request.headers["Authorization"] == "Bearer fresh"


def test_dlt_tables_take_the_catalog_location(monkeypatch):
    pytest.importorskip("dlt")
    import dlt.common.libs.pyiceberg as dlt_iceberg

    from user_portal.notebook import connection

    calls = []
    monkeypatch.setattr(dlt_iceberg, "create_table", lambda catalog, table_id, location, *a, **kw: calls.append(location))
    connection.patch_dlt_table_location()
    connection.patch_dlt_table_location()
    dlt_iceberg.create_table("catalog", "raw.stations", "file:///tmp/local/raw/stations/", schema=None)
    # Wrapped once, and Polaris chooses the location inside the database's bucket.
    assert calls == [None]


def test_dlt_notebook_loads_and_merges_without_duplicates(monkeypatch, tmp_path):
    pytest.importorskip("marimo")
    pytest.importorskip("dlt")
    import importlib.util
    from pathlib import Path

    import dlt
    import dlt.common.libs.pyiceberg as dlt_iceberg
    from pyiceberg.catalog.sql import SqlCatalog

    from user_portal.notebook import connection

    # Undo the notebook's wrapper after the test.
    monkeypatch.setattr(dlt_iceberg, "create_table", dlt_iceberg.create_table)
    config = {"type": "sql", "uri": f"sqlite:///{tmp_path}/catalog.db", "warehouse": f"file://{tmp_path}/warehouse"}
    monkeypatch.setenv("ICEBERG_DATABASE", "test_database")
    monkeypatch.setenv("RUNTIME__DLTHUB_TELEMETRY", "false")

    def local_destination(local_dir):
        # The notebook's destination, with a local SQL catalog in place of Polaris.
        dlt.config["iceberg_catalog.iceberg_catalog_type"] = "sql"
        dlt.config["iceberg_catalog.iceberg_catalog_config"] = dict(config)
        connection.patch_dlt_table_location()
        return dlt.destinations.filesystem(bucket_url=f"file://{local_dir}")

    monkeypatch.setattr("user_portal.notebook.connection.dlt_destination", local_destination)
    monkeypatch.setattr("user_portal.notebook.connection.connect", lambda: SqlCatalog("default", **config))
    path = Path(__file__).parents[1] / "user_portal/notebook/examples/08_dlt_ingest.py"
    spec = importlib.util.spec_from_file_location("dlt_ingest", path)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    first = notebook.app.run()[1]
    second = notebook.app.run()[1]
    assert first["load_message"] == "Loaded 12 stations and 24 sensors. The table now holds 12 stations."
    assert second["load_message"] == "Loaded 12 stations and 24 sensors. The table now holds 12 stations."
    stations = second["stored_table"].scan().to_arrow()
    assert sorted(stations["station_id"].to_pylist()) == list(range(1, 13))
    assert {"_dlt_id", "_dlt_load_id"} <= set(stations.column_names)
    # The bookkeeping stayed in the run's local folder.
    assert SqlCatalog("default", **config).list_tables("raw_dlt_demo") == [
        ("raw_dlt_demo", "stations"), ("raw_dlt_demo", "stations__sensors")]
