"""Query language boundaries and durable, scoped definition editing."""

from types import SimpleNamespace

import duckdb
import pytest

from server.models import ServiceError
from user_portal.reporting.definitions import Builder, Dimension, Filter, builder_sql, validate_sql
from user_portal.reporting.store import ReportStore


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_parquet('/tmp/private')",
        "SELECT * FROM 'https://example.com/data.csv'",
        "SELECT * FROM duckdb_secrets()",
        "SELECT getenv('SECRET')",
        "SELECT current_setting('secret_directory')",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM other_table",
        "ATTACH 'x' AS source",
        "SELECT 1; SELECT 2",
        "COPY source TO '/tmp/result'",
        "SELECT * INTO result FROM source",
        "INSTALL httpfs",
        "LOAD httpfs",
        "PRAGMA version",
        "UPDATE source SET id=1",
        "SELECT * FROM query('SELECT * FROM read_blob(?)')",
        "SELECT * FROM source AT (VERSION => 1)",
        "WITH source AS (SELECT 42) SELECT * FROM source",
        "WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x",
        "WITH x AS (SELECT * FROM read_csv('/tmp/a')) SELECT * FROM x",
        "SELECT * FROM source UNION SELECT * FROM sqlite_scan('/tmp/x', 't')",
    ],
)
def test_sql_rejects_external_access_writes_introspection_and_dynamic_queries(sql):
    with pytest.raises(ValueError):
        validate_sql(sql, {})


def test_sql_and_visual_queries_bind_values_in_real_duckdb():
    connection = duckdb.connect()
    connection.execute("CREATE TABLE source (street VARCHAR, energy DOUBLE, ts TIMESTAMP)")
    connection.execute("INSERT INTO source VALUES ('Main', 2.5, '2026-09-01'), ('Other', 8, '2026-09-01')")
    builder = Builder(
        aggregate="sum",
        measure="energy",
        dimensions=[Dimension(column="street")],
        filters=[Filter(column="street", value="Main")],
        sort="value",
        descending=True,
    )
    sql, parameters = builder_sql(builder)
    assert connection.execute(sql, parameters).fetchall() == [("Main", 2.5)]
    assert connection.execute(validate_sql(sql, parameters), parameters).fetchall() == [("Main", 2.5)]
    contains = Builder(aggregate="count", filters=[Filter(column="street", operator="contains", value="Mai")])
    sql, parameters = builder_sql(contains)
    assert connection.execute(validate_sql(sql, parameters), parameters).fetchall() == [(1,)]
    injected = "Main' OR true --"
    sql, parameters = builder_sql(
        builder.model_copy(update={"filters": [Filter(column="street", value=injected)]})
    )
    assert injected not in sql and connection.execute(sql, parameters).fetchall() == []
    sql = "WITH daily AS (SELECT date_trunc('day', ts) AS day, sum(energy) AS value FROM source WHERE street=$street GROUP BY 1) SELECT * FROM daily"
    assert connection.execute(validate_sql(sql, {"street": "Main"}), {"street": "Main"}).fetchone()[1] == 2.5
    with pytest.raises(ValueError, match="parameter"):
        validate_sql(sql, {})
    with pytest.raises(ValueError, match="parameter"):
        validate_sql("SELECT count(*) FROM source", {"unused": 42})
    connection.close()


def test_definition_store_survives_restart_and_detects_conflicts(tmp_path):
    path = tmp_path / "reports.sqlite"
    alice = SimpleNamespace(user_id="alice", team="team", environment="development")
    bob = SimpleNamespace(user_id="bob", team="team", environment="development")
    store = ReportStore(path)
    first = store.save(alice, "reader", "report", {"name": "Energy"})
    store.close()
    store = ReportStore(path)
    assert store.get(bob, first["id"])["body"] == {"name": "Energy"}
    with pytest.raises(ServiceError) as error:
        store.save(bob, "reader", "report", {"name": "Other"}, first["id"], 1)
    assert error.value.status == 403
    second = store.save(alice, "reader", "report", {"name": "Updated"}, first["id"], 1)
    assert second["revision"] == 2
    with pytest.raises(ServiceError) as error:
        store.save(alice, "reader", "report", {"name": "Stale"}, first["id"], 1)
    assert error.value.status == 409
    bob.environment = "production"
    assert store.list(bob) == []
    with pytest.raises(ServiceError):
        store.get(bob, first["id"])
    dashboard = store.save(
        alice, "reader", "dashboard", {"name": "Board", "cards": [{"report_id": first["id"]}]}
    )
    with pytest.raises(ServiceError, match="dashboards"):
        store.delete(alice, "reader", first["id"], 2)
    store.delete(alice, "reader", dashboard["id"], 1)
    store.delete(alice, "reader", first["id"], 2)
    assert store.list(alice) == []
    store.close()
