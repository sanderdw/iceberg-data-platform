"""Preview resource boundary, process isolation and DuckDB's rendering of rows."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.models import ServiceError
from user_portal.catalog import columns
from user_portal.preview import read_rows, run_preview


def test_nested_schema_fields_keep_ids_and_types():
    result = columns(
        {
            "fields": [
                {
                    "id": 1,
                    "name": "items",
                    "type": {
                        "type": "list",
                        "element-id": 2,
                        "element": {
                            "type": "struct",
                            "fields": [
                                {"id": 3, "name": "value", "type": "decimal(10, 2)", "required": True}
                            ],
                        },
                    },
                }
            ]
        }
    )
    assert [(c["id"], c["name"], c["type"]) for c in result] == [
        (1, "items", "list"),
        (2, "items.element", "struct"),
        (3, "items.element.value", "decimal(10, 2)"),
    ]


def test_preview_process_does_not_inherit_platform_secrets(monkeypatch):
    monkeypatch.setenv("POLARIS_CLIENT_SECRET", "platform-secret")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"rows": []}'))
    monkeypatch.setattr(subprocess, "run", run)
    assert run_preview({"token": "user-token"}) == {"rows": []}
    args, kwargs = run.call_args
    assert "platform-secret" not in str(kwargs)
    assert "user-token" not in str(args)
    assert json.loads(kwargs["input"])["token"] == "user-token"
    assert kwargs["timeout"] == 30
    run.return_value = SimpleNamespace(returncode=1, stdout="provider secret", stderr="storage secret")
    with pytest.raises(ServiceError, match="Preview unavailable"):
        run_preview({})
    run.side_effect = subprocess.TimeoutExpired("preview", 30)
    with pytest.raises(ServiceError, match="30 seconds"):
        run_preview({})


def test_a_crashing_preview_never_reaches_the_portal_process():
    # A real child process: no catalog is listening, so it fails, and only it fails.
    request = {
        "uri": "http://127.0.0.1:9",
        "database": "db",
        "namespace": ["analytics"],
        "table": "events",
        "snapshotId": None,
        "limit": 100,
        "token": "user-token",
        "s3Endpoint": "http://127.0.0.1:9",
    }
    with pytest.raises(ServiceError, match="Preview unavailable") as error:
        run_preview(request)
    assert error.value.status == 502 and "user-token" not in str(error.value)


def test_a_preview_killed_by_the_kernel_is_reported_as_unavailable(monkeypatch):
    killed = SimpleNamespace(returncode=-9, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=killed))
    with pytest.raises(ServiceError, match="Preview unavailable"):
        run_preview({})


def test_rows_are_bounded_and_every_type_is_rendered_as_text():
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect()
    connection.execute("""
        CREATE TABLE events AS
        SELECT range AS id,
               9007199254740993 AS large_id,
               CASE WHEN range < 149 THEN repeat('x', 600) END AS event,
               CASE WHEN range > 0 THEN {'kind': 'door', 'who': {'badge': 7}}::VARIANT END AS payload,
               'POINT (5 52)'::GEOMETRY AS location,
               TIMESTAMP_NS '2026-09-21 14:13:20.000066225' AS measured_at
        FROM range(150)
    """)
    names, rows, columns_truncated, cells_truncated = read_rows(connection, "events ORDER BY id", 100)
    assert names == ["id", "large_id", "event", "payload", "location", "measured_at"]
    assert len(rows) == 100 and cells_truncated and not columns_truncated
    assert rows[0][:2] == ["0", "9007199254740993"]  # Exact, beyond JavaScript's safe integers.
    assert len(rows[0][2]) == 513 and rows[0][2].endswith("…")
    assert rows[0][3] is None and rows[1][3] == '{"kind":"door","who":{"badge":7}}'
    assert rows[0][4:] == ["POINT (5 52)", "2026-09-21 14:13:20.000066225"]
    assert read_rows(connection, "events WHERE id = 149", 100)[1][0][2] is None
    # An empty table has columns but no rows, whatever is committed meanwhile.
    assert read_rows(connection, "events", 0)[:2] == (names, [])


def test_wide_tables_show_the_first_fifty_columns():
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect()
    connection.execute(f"CREATE TABLE wide AS SELECT {', '.join(f'{i} AS c{i}' for i in range(60))}")
    names, rows, columns_truncated, _ = read_rows(connection, "wide", 100)
    assert names == [f"c{i}" for i in range(50)] and len(rows[0]) == 50 and columns_truncated


def test_quoted_column_names_cannot_inject_sql():
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect()
    connection.execute('CREATE TABLE odd AS SELECT 1 AS "a"" AS VARCHAR), (SELECT 42) AS (""b"')
    names, rows, _, _ = read_rows(connection, "odd", 100)
    assert names == ['a" AS VARCHAR), (SELECT 42) AS ("b'] and rows == [["1"]]
