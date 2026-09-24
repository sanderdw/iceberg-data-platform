import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="03 · Native DuckDB on Iceberg", sql_output="pandas")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import connect_duckdb, table_reference

    return (connect_duckdb, json, mo, os, table_reference, plain_table)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Native DuckDB on Iceberg
    **Example 3 of 7**

    Attach the selected Polaris database as **lakehouse**, then query Iceberg directly
    with DuckDB SQL. DuckDB reads the manifests and Parquet files from RustFS using
    your own permissions. Only query results are brought into this notebook.

    The selected portal table is used by default; otherwise this opens
    `synthetic.neighborhood_electricity` from example 1. Run that example first if needed.
    This connection is read-only.
    """)


@app.cell
def _(json, os):
    # Edit these values to query another table.
    NAMESPACE = (
        json.loads(os.environ.get("ICEBERG_NAMESPACE", "[]"))
        if os.environ.get("ICEBERG_TABLE")
        else ["synthetic"]
    )
    TABLE = os.environ.get("ICEBERG_TABLE") or "neighborhood_electricity"
    return NAMESPACE, TABLE


@app.cell
def _(NAMESPACE, TABLE, connect_duckdb, mo, table_reference):
    if not NAMESPACE or not all(NAMESPACE) or not TABLE:
        raise ValueError("Set NAMESPACE and TABLE in the settings cell.")
    try:
        lakehouse = connect_duckdb(NAMESPACE, TABLE)
    except RuntimeError as _error:
        mo.stop(True, mo.callout(str(_error), kind="warn"))
    selected_table = table_reference(NAMESPACE, TABLE)
    lakehouse.execute(f"CREATE VIEW selected_iceberg_table AS SELECT * FROM {selected_table}")
    mo.md(f"Connected to `{selected_table}`. The SQL cells below use this DuckDB connection.")
    return lakehouse, selected_table


@app.cell
def _(lakehouse, mo, plain_table):
    tables = mo.sql("SHOW ALL TABLES", engine=lakehouse, output=False)
    plain_table(tables)
    return (tables,)


@app.cell
def _(lakehouse, mo, plain_table):
    columns = mo.sql("DESCRIBE selected_iceberg_table", engine=lakehouse, output=False)
    plain_table(columns)
    return (columns,)


@app.cell
def _(lakehouse, mo, plain_table):
    preview = mo.sql("SELECT * FROM selected_iceberg_table LIMIT 100", engine=lakehouse, output=False)
    plain_table(preview)
    return (preview,)


@app.cell
def _(lakehouse, mo, plain_table):
    row_count = mo.sql("SELECT count(*) AS rows FROM selected_iceberg_table", engine=lakehouse, output=False)
    plain_table(row_count)
    return (row_count,)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    snapshots = mo.sql(f"SELECT * FROM iceberg_snapshots({selected_table})", engine=lakehouse, output=False)
    plain_table(snapshots)
    return (snapshots,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Aggregate the neighborhood example
    This query runs when the selected table has the example's energy columns.
    Edit the SQL to explore your own data; `selected_iceberg_table` is a local view
    over the attached Iceberg table, so filters and aggregations execute in DuckDB.
    """)


@app.cell
def _(columns, lakehouse, mo, plain_table):
    mo.stop(
        not {"street", "home_id", "consumption_kwh", "generation_kwh"}.issubset(columns["column_name"]),
        mo.md("Select the neighborhood example table to run the energy aggregation."),
    )
    street_totals = mo.sql(
        """
        SELECT street, count(DISTINCT home_id) AS homes,
               round(sum(consumption_kwh), 2) AS consumption_kwh,
               round(sum(generation_kwh), 2) AS generation_kwh
        FROM selected_iceberg_table
        GROUP BY street
        ORDER BY street
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(street_totals)
    return (street_totals,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Refresh and explore
    Rerun the connection cell after another notebook changes the table,
    or when temporary credentials expire. Credentials stay in this process's memory.
    Reconnect for another table to obtain its scoped storage credentials.

    To inspect history, use a snapshot ID from the snapshot cell in a SQL query:
    `SELECT * FROM lakehouse.synthetic.neighborhood_electricity AT (VERSION => 123) LIMIT 100`
    (replace `123` with a real snapshot ID).

    Connection setup is in `user_portal.notebook.duckdb_connection`: it uses
    `CREATE SECRET` and `ATTACH ... (TYPE iceberg)` with the workspace environment.
    See [DuckDB's Iceberg REST catalog documentation](https://duckdb.org/docs/lts/core_extensions/iceberg/iceberg_rest_catalogs).
    """)


if __name__ == "__main__":
    app.run()
