"""SQL an LLM wrote for the evaluation's control arm reaches only the bound tables."""

import pytest

from conversationalbi.errors import CbiError
from conversationalbi.semantic import sql_guard, worker

VIEWS = {"flights", "carriers"}


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('s3://other-bucket/x.parquet')",
    "SELECT * FROM read_text('/proc/self/environ')",
    "SELECT * FROM glob('/**')",
    "SELECT getenv('PATH')",
    "SELECT * FROM duckdb_secrets()",
    "SELECT current_setting('s3_secret_access_key')",
    "SELECT * FROM 'x.parquet'",
    "SELECT * FROM 'http://internal:8181/api'",
    "SELECT * FROM \"s3://bucket/data.parquet\"",
    "COPY flights TO '/tmp/x.csv'",
    "ATTACH '/tmp/x.db' AS x",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA database_list",
    "SET memory_limit='1TB'",
    "CREATE TABLE t AS SELECT 1",
    "DROP VIEW flights",
    "INSERT INTO flights VALUES (1)",
    "SELECT 1; SELECT 2",
    "SELECT * FROM secrets",
    "SELECT * FROM main.flights",
    "SELECT * FROM information_schema.tables",
    "WITH t AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM t",
    "SELECT (SELECT getenv('PATH')) FROM flights",
    "SELECT * FROM flights WHERE id IN (SELECT id FROM other)",
    "",
    "not sql at all (",
])
def test_refused(sql):
    with pytest.raises(CbiError) as found:
        sql_guard.check(sql, VIEWS)
    assert found.value.code == "unsafe_sql"


@pytest.mark.parametrize("sql", [
    "SELECT c.name, avg(f.dep_delay) AS d FROM flights f JOIN carriers c ON f.carrier_code = c.code GROUP BY 1 "
    "ORDER BY 2 DESC LIMIT 5",
    "WITH t AS (SELECT * FROM flights WHERE NOT cancelled) SELECT count(*) FROM t",
    "SELECT 100.0 * count(*) FILTER (WHERE arr_delay < 15) / nullif(count(*), 0) FROM FLIGHTS",
    "SELECT date_trunc('week', date), avg(dep_delay) FROM flights GROUP BY 1",
    "SELECT name, row_number() OVER (ORDER BY name) FROM carriers",
    "SELECT name FROM carriers UNION ALL SELECT name FROM carriers",
    "SELECT round(avg(dep_delay), 2) FROM flights WHERE carrier_code IN (SELECT code FROM carriers)",
])
def test_allowed(sql):
    assert sql_guard.check(sql, VIEWS).upper().lstrip().startswith(("SELECT", "WITH"))


def test_only_the_arm_that_asks_gets_sql_errors(monkeypatch):
    import duckdb

    monkeypatch.setattr(worker, "load_tables", lambda job: [])
    monkeypatch.setattr(worker, "bind", lambda connection, job, loaded: {})
    monkeypatch.setattr(worker, "lock", lambda connection: None)
    monkeypatch.setattr(worker, "connect", duckdb.connect)
    job = {"sql": "SELECT nope", "params": [], "limit": 10}
    with pytest.raises(duckdb.BinderException):
        worker.run(job)
    found = worker.run({**job, "explainSqlErrors": True})
    assert found["error"] == "sql" and "nope" in found["message"]
