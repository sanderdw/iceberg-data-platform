import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="05 · Read Iceberg v3 with DuckDB", sql_output="pandas")


@app.cell
def _():
    import marimo as mo

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import connect_duckdb, table_reference

    NAMESPACE = ["iceberg_v3"]
    TABLE = "sensor_events"
    return (NAMESPACE, TABLE, connect_duckdb, mo, table_reference, plain_table)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Read Iceberg v3 with DuckDB
    **Example 5 of 7**

    Query the Iceberg **format-version 3** table from example 4 directly with DuckDB
    SQL. Each section shows one thing v3 adds. Run **04 · Write Iceberg v3 with
    DuckDB** first; this notebook then runs from top to bottom without any input.

    The connection is read-only and uses your own permissions. Change `NAMESPACE` and
    `TABLE` in the first cell to read another table.
    """)


@app.cell
def _(NAMESPACE, TABLE, connect_duckdb, table_reference):
    lakehouse = connect_duckdb(NAMESPACE, TABLE)
    selected_table = table_reference(NAMESPACE, TABLE)
    return lakehouse, selected_table


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    columns = mo.sql(f"DESCRIBE {selected_table}", engine=lakehouse, output=False)
    plain_table(columns)
    return (columns,)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    row_count = mo.sql(f"SELECT count(*) AS rows FROM {selected_table}", engine=lakehouse, output=False)
    plain_table(row_count)
    return (row_count,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 1. Variant
    `payload` holds a different structure per event, without a fixed schema. Address
    fields with dots, also nested ones, and cast them to the type you need. A field
    that an event does not have is `NULL`.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    kinds = mo.sql(
        f"""
        SELECT payload.kind::VARCHAR AS kind,
               count(*) AS events,
               round(avg(payload.celsius::DOUBLE), 2) AS avg_celsius,
               round(max(payload.axes.x::DOUBLE), 3) AS max_vibration_x,
               count(*) FILTER (WHERE payload.open::BOOLEAN) AS doors_open
        FROM {selected_table}
        GROUP BY kind
        ORDER BY events DESC
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(kinds)
    return (kinds,)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    payloads = mo.sql(
        f"""
        SELECT event_id, device_id, variant_typeof(payload) AS shape, payload
        FROM {selected_table}
        ORDER BY event_id
        LIMIT 8
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(payloads)
    return (payloads,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Nanosecond timestamps
    `TIMESTAMP_NS` keeps nine fractional digits. Truncated to microseconds, the limit
    of Iceberg v2, events of one sensor collide.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    precision = mo.sql(
        f"""
        SELECT count(*) AS events,
               count(DISTINCT (device_id, measured_at)) AS distinct_at_nanoseconds,
               count(DISTINCT (device_id, measured_at::TIMESTAMP)) AS distinct_at_microseconds
        FROM {selected_table}
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(precision)
    return (precision,)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    bursts = mo.sql(
        f"""
        SELECT device_id, strftime(measured_at, '%H:%M:%S.%n') AS measured_at_ns,
               epoch_ns(measured_at) - lag(epoch_ns(measured_at)) OVER (
                   PARTITION BY device_id ORDER BY measured_at
               ) AS nanoseconds_since_previous
        FROM {selected_table}
        ORDER BY device_id, measured_at
        LIMIT 8
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(bursts)
    return (bursts,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Geometry
    `location` is stored as the Iceberg `geometry` type. DuckDB reads it as `GEOMETRY`
    and shows it as WKT text.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    locations = mo.sql(
        f"""
        SELECT site, location::VARCHAR AS location, count(DISTINCT device_id) AS sensors
        FROM {selected_table}
        GROUP BY ALL
        ORDER BY site, location
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(locations)
    return (locations,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. Default values
    `firmware` was added after the first 480 rows were written. Those data files do
    not contain the column; the value `v1.0` comes from the default in the table
    metadata. `v2.0` was written by the update in example 4.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
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


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. Row lineage
    Every v3 row has a stable `_row_id` and the sequence number of the commit that
    last changed it. The rows of `SENSOR-001` were updated in a later commit than the
    insert, which makes incremental processing possible without comparing data.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    lineage = mo.sql(
        f"""
        SELECT _last_updated_sequence_number AS last_updated_in_commit,
               count(*) AS events,
               min(_row_id) AS first_row_id,
               max(_row_id) AS last_row_id,
               string_agg(DISTINCT device_id, ', ' ORDER BY device_id)
                   FILTER (WHERE firmware = 'v2.0') AS updated_sensors
        FROM {selected_table}
        GROUP BY ALL
        ORDER BY last_updated_in_commit
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(lineage)
    return (lineage,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. Deletion vectors
    The update and the delete in example 4 did not rewrite the first data file. v3
    records removed row positions in binary deletion vectors, stored as Puffin files.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    files = mo.sql(
        f"""
        SELECT manifest_content, content, file_format, record_count,
               split_part(file_path, '/', -1) AS file
        FROM iceberg_metadata({selected_table})
        ORDER BY manifest_content, record_count DESC
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(files)
    return (files,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 7. Time travel
    Every write is a snapshot. The first snapshot holds the table before the default
    value, the update and the delete: all 480 events, including the fault events.
    """)


@app.cell
def _(lakehouse, mo, selected_table, plain_table):
    snapshots = mo.sql(
        f"SELECT sequence_number, snapshot_id, timestamp_ms FROM iceberg_snapshots({selected_table})",
        engine=lakehouse,
        output=False,
    )
    plain_table(snapshots)
    return (snapshots,)


@app.cell
def _(lakehouse, mo, selected_table, snapshots, plain_table):
    _first = int(snapshots.sort_values("sequence_number")["snapshot_id"].iloc[0])
    first_snapshot = mo.sql(
        f"""
        SELECT payload.kind::VARCHAR AS kind, count(*) AS events
        FROM {selected_table} AT (VERSION => {_first})
        GROUP BY kind
        ORDER BY events DESC
        """,
        engine=lakehouse,
        output=False,
    )
    plain_table(first_snapshot)
    return (first_snapshot,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Explore further
    Edit any SQL cell and run it again. The table preview in the portal does not
    support `VARIANT` columns yet; use this notebook to inspect the table.

    Connection setup is in `user_portal.notebook.duckdb_connection`. See
    [DuckDB's Iceberg extension](https://duckdb.org/docs/current/core_extensions/iceberg/overview)
    and the [Iceberg v3 types](https://iceberg.apache.org/spec/#version-3-extended-types-and-capabilities).
    """)


if __name__ == "__main__":
    app.run()
