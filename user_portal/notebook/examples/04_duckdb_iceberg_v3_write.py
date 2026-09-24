import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="04 · Write Iceberg v3 with DuckDB", sql_output="pandas")


@app.cell
def _():
    import marimo as mo

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import (
        EXAMPLE_PROPERTY,
        connect_duckdb,
        drop_example_table,
        refresh_table_credentials,
        schema_reference,
        table_reference,
    )
    from user_portal.notebook.synthetic import generate_sensor_events

    NAMESPACE = ["iceberg_v3"]
    TABLE = "sensor_events"
    EXAMPLE = "04_duckdb_iceberg_v3_write"
    return (
        EXAMPLE,
        EXAMPLE_PROPERTY,
        NAMESPACE,
        TABLE,
        connect_duckdb,
        drop_example_table,
        generate_sensor_events,
        mo,
        refresh_table_credentials,
        schema_reference,
        table_reference,
        plain_table,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Write Iceberg v3 with DuckDB
    **Example 4 of 7**

    DuckDB creates and writes an Iceberg **format-version 3** table through Polaris,
    using your own permissions. The table uses what v3 adds: a `VARIANT` column for
    free-form payloads, a nanosecond `TIMESTAMP_NS`, a `GEOMETRY` location, a column
    default value, row lineage and a binary deletion vector.

    This notebook runs from top to bottom without any input. It recreates
    `iceberg_v3.sensor_events` on every run, so the result is always the same, and it
    touches no other table. It marks the table as its own with a table property and
    stops if a table with that name exists without the mark, instead of replacing it.
    Writing requires a writer role or higher in the active team. Change `NAMESPACE`
    and `TABLE` in the first cell to write somewhere else.
    """)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 1. Generate the data
    480 fictional sensor events. Every payload has its own shape, bursts of events
    fall within one microsecond, and every sensor has a location.
    """)


@app.cell
def _(generate_sensor_events, plain_table):
    events = generate_sensor_events()
    plain_table(events.slice(0, 8))
    return (events,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Connect and create the v3 table
    The connection is writable. `WITH ('format-version' = 3)` selects Iceberg v3;
    Polaris writes the table metadata, so DuckDB needs no storage access yet.
    """)


@app.cell
def _(NAMESPACE, TABLE, connect_duckdb, schema_reference, table_reference):
    lakehouse = connect_duckdb(NAMESPACE, TABLE, read_only=False, missing_ok=True)
    selected_schema = schema_reference(NAMESPACE)
    selected_table = table_reference(NAMESPACE, TABLE)
    return lakehouse, selected_schema, selected_table


@app.cell
def _(
    EXAMPLE,
    EXAMPLE_PROPERTY,
    NAMESPACE,
    TABLE,
    drop_example_table,
    lakehouse,
    selected_schema,
    selected_table,
):
    lakehouse.execute(f"CREATE SCHEMA IF NOT EXISTS {selected_schema}")
    # Replaces only a table that an earlier run of this example created.
    drop_example_table(lakehouse, NAMESPACE, TABLE, EXAMPLE)
    lakehouse.execute(f"""
        CREATE TABLE {selected_table} (
            event_id BIGINT,
            device_id VARCHAR,
            site VARCHAR,
            location GEOMETRY,
            measured_at TIMESTAMP_NS,
            payload VARIANT,
            synthetic BOOLEAN
        ) WITH ('format-version' = 3, '{EXAMPLE_PROPERTY}' = '{EXAMPLE}')
    """)
    table_created = True
    return (table_created,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Insert the events
    Polaris vends temporary storage credentials for the new table. The JSON text
    becomes `VARIANT`, the WKT text becomes `GEOMETRY`, and the Arrow nanosecond
    timestamps are stored as `TIMESTAMP_NS` without losing digits.
    """)


@app.cell
def _(NAMESPACE, TABLE, events, lakehouse, mo, refresh_table_credentials, selected_table, table_created):
    assert table_created
    refresh_table_credentials(lakehouse, NAMESPACE, TABLE)
    lakehouse.register("events", events)
    inserted_rows = lakehouse.execute(f"""
        INSERT INTO {selected_table}
        SELECT event_id, device_id, site, location_wkt::GEOMETRY, measured_at,
               payload::JSON::VARIANT, synthetic
        FROM events
    """).fetchone()[0]
    mo.md(f"Inserted **{inserted_rows}** events into `{selected_table}`.")
    return (inserted_rows,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. Add a column with a default value
    In v3 a new column can carry a default. The 480 rows already written are not
    rewritten: readers fill in `v1.0` from the table metadata.
    """)


@app.cell
def _(inserted_rows, lakehouse, selected_table):
    assert inserted_rows == 480
    lakehouse.execute(f"ALTER TABLE {selected_table} ADD COLUMN firmware VARCHAR DEFAULT 'v1.0'")
    lakehouse.execute(f"""
        INSERT INTO {selected_table} (event_id, device_id, site, location, measured_at, payload, synthetic)
        VALUES (481, 'SENSOR-013', 'Example Depot', 'POINT (5.2 52.04)'::GEOMETRY,
                TIMESTAMP_NS '2026-09-21 15:00:00.123456789',
                {{'kind': 'door', 'open': true, 'badge': 4242}}::VARIANT, true)
    """)
    default_added = True
    return (default_added,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. Update and delete rows
    The update and the delete do not rewrite the data files. A v3 table records the
    removed row positions in a compact binary **deletion vector** (a Puffin file)
    instead of the Parquet delete files of v2.
    """)


@app.cell
def _(default_added, lakehouse, mo, selected_table):
    assert default_added
    updated_rows = lakehouse.execute(
        f"UPDATE {selected_table} SET firmware = 'v2.0' WHERE device_id = 'SENSOR-001'"
    ).fetchone()[0]
    deleted_rows = lakehouse.execute(
        f"DELETE FROM {selected_table} WHERE payload.kind::VARCHAR = 'fault'"
    ).fetchone()[0]
    mo.md(f"Updated **{updated_rows}** events of `SENSOR-001` and deleted **{deleted_rows}** fault events.")
    return deleted_rows, updated_rows


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. Result
    The rows that remain, per firmware version, and the snapshot of every write.
    """)


@app.cell
def _(deleted_rows, lakehouse, mo, selected_table, plain_table):
    assert deleted_rows
    firmware = mo.sql(
        f"""
        SELECT firmware, count(*) AS events
        FROM {selected_table}
        GROUP BY firmware
        ORDER BY firmware
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(firmware)
    return (firmware,)


@app.cell
def _(firmware, lakehouse, mo, selected_table, plain_table):
    assert len(firmware)
    snapshots = mo.sql(
        f"SELECT sequence_number, snapshot_id, timestamp_ms FROM iceberg_snapshots({selected_table})",
        engine=lakehouse,
        output=False,
    )
    plain_table(snapshots)
    return (snapshots,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Continue with example 5
    **05 · Read Iceberg v3 with DuckDB** queries this table: fields inside the
    `VARIANT`, the nanosecond digits, the geometry, the default value, row lineage and
    the deletion vector.

    Not yet in DuckDB: the v3 types `GEOGRAPHY` and `UNKNOWN` are planned for DuckDB
    2.0, and DuckDB has no nanosecond timestamp with time zone (`timestamptz_ns`).
    See [Writing to Iceberg](https://duckdb.org/docs/current/core_extensions/iceberg/writing_to_iceberg)
    and the [Iceberg v3 types](https://iceberg.apache.org/spec/#version-3-extended-types-and-capabilities).
    """)


if __name__ == "__main__":
    app.run()
