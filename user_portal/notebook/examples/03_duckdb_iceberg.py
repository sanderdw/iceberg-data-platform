import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="03 · Native DuckDB on Iceberg", sql_output="pandas")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    from user_portal.notebook.duckdb_connection import connect_duckdb, table_reference

    return connect_duckdb, json, mo, os, table_reference


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Native DuckDB on Iceberg
    **Example 3 of 3**

    Attach the selected Polaris database as **lakehouse**, then query Iceberg directly
    with DuckDB SQL. DuckDB reads the manifests and Parquet files from RustFS using
    your own permissions. Only query results are brought into this notebook.

    The selected portal table is used by default; otherwise this opens
    `synthetic.neighborhood_electricity` from example 1. Run that example first if needed.
    This connection is read-only.
    """)


@app.cell(hide_code=True)
def _(json, mo, os):
    _selected = os.environ.get("ICEBERG_TABLE")
    table_settings = mo.ui.dictionary(
        {
            "namespace": mo.ui.text(
                value=(
                    ".".join(json.loads(os.environ.get("ICEBERG_NAMESPACE", "[]")))
                    if _selected
                    else "synthetic"
                ),
                label="Namespace (dots separate nested levels)",
            ),
            "table": mo.ui.text(
                value=os.environ.get("ICEBERG_TABLE") or "neighborhood_electricity", label="Table"
            ),
        }
    ).form(submit_button_label="Connect / refresh credentials", show_clear_button=False)
    mo.vstack([mo.md("### Choose a table"), table_settings])
    return (table_settings,)


@app.cell
def _(connect_duckdb, mo, table_reference, table_settings):
    mo.stop(
        table_settings.value is None, mo.md("Choose a table and click **Connect / refresh credentials**.")
    )
    _namespace = tuple(table_settings.value["namespace"].strip().split("."))
    _table = table_settings.value["table"].strip()
    mo.stop(not all(_namespace) or not _table, mo.md("Enter a namespace and table name."))
    try:
        lakehouse = connect_duckdb(_namespace, _table)
    except RuntimeError as _error:
        mo.stop(True, mo.callout(str(_error), kind="warn"))
    selected_table = table_reference(_namespace, _table)
    lakehouse.execute(f"CREATE VIEW selected_iceberg_table AS SELECT * FROM {selected_table}")
    mo.md(f"Connected to `{selected_table}`. The SQL cells below use this DuckDB connection.")
    return lakehouse, selected_table


@app.cell
def _(lakehouse, mo):
    tables = mo.sql("SHOW ALL TABLES", engine=lakehouse)
    return (tables,)


@app.cell
def _(lakehouse, mo):
    columns = mo.sql("DESCRIBE selected_iceberg_table", engine=lakehouse)
    return (columns,)


@app.cell
def _(lakehouse, mo):
    preview = mo.sql("SELECT * FROM selected_iceberg_table LIMIT 100", engine=lakehouse)
    return (preview,)


@app.cell
def _(lakehouse, mo):
    row_count = mo.sql("SELECT count(*) AS rows FROM selected_iceberg_table", engine=lakehouse)
    return (row_count,)


@app.cell
def _(lakehouse, mo, selected_table):
    snapshots = mo.sql(f"SELECT * FROM iceberg_snapshots({selected_table})", engine=lakehouse)
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
def _(columns, lakehouse, mo):
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
    )
    return (street_totals,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Refresh and explore
    Click **Connect / refresh credentials** after another notebook changes the table,
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
