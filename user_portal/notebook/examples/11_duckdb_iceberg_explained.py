import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium", app_title="11 · How Apache Iceberg works")


@app.cell(hide_code=True)
def _():
    from datetime import datetime, timezone

    import marimo as mo
    from pyarrow.fs import FileSelector

    from user_portal.notebook.connection import connect
    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import (
        EXAMPLE_PROPERTY,
        connect_duckdb,
        drop_example_table,
        refresh_table_credentials,
        schema_reference,
        table_reference,
    )

    NAMESPACE = ["rickandmorty"]
    TABLE = "characters"
    EXAMPLE = "11_duckdb_iceberg_explained"
    characters = table_reference(NAMESPACE, TABLE)

    def short(path):
        return path.split(f"/{TABLE}/", 1)[-1]

    def snapshots(lakehouse):
        rows = lakehouse.sql(f"""
            SELECT sequence_number, snapshot_id, operation
            FROM iceberg_snapshots({characters}) ORDER BY sequence_number
        """).df()
        return plain_table(rows, label="Snapshots")

    def files(lakehouse):
        rows = lakehouse.sql(f"""
            SELECT manifest_content AS kind, record_count AS rows, file_path AS file
            FROM iceberg_metadata({characters})
        """).df()
        rows["file"] = rows["file"].map(short)
        return plain_table(rows, label="Files in the current snapshot")

    def objects(table):
        """Every object under the table's location in the bucket."""
        location = table.location().removeprefix("s3://")
        storage = table.io.fs_by_scheme("s3", location.split("/")[0])
        listing = storage.get_file_info(FileSelector(location, recursive=True))
        return {"s3://" + item.path for item in listing if item.type.name == "File"}

    return (
        EXAMPLE,
        EXAMPLE_PROPERTY,
        NAMESPACE,
        TABLE,
        characters,
        connect,
        connect_duckdb,
        drop_example_table,
        datetime,
        files,
        mo,
        objects,
        plain_table,
        refresh_table_credentials,
        schema_reference,
        short,
        snapshots,
        timezone,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # How Apache Iceberg works
    **Example 11 of 11 · Snapshots, time travel and maintenance with DuckDB and PyIceberg**

    Iceberg is not a database but an open table format. The data is stored as
    Parquet files in a bucket. Next to it, metadata files record which files
    form the table at each moment. The catalog, Apache Polaris here, only keeps a
    pointer to the current metadata file.

    This notebook uses DuckDB. PyIceberg, Polars, Spark or Snowflake read and write
    the same table, because they all follow the same standard. It reads the
    characters from the public Rick and Morty API, so it needs internet access.
    """)


@app.cell
def _(NAMESPACE, TABLE, connect_duckdb):
    lakehouse = connect_duckdb(NAMESPACE, TABLE, read_only=False, missing_ok=True)
    return (lakehouse,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 1. Create a table
    DuckDB reads every character from the Rick and Morty API and writes them to
    the bucket as Parquet. Polaris records the new metadata file.
    """)


@app.cell
def _(
    EXAMPLE,
    EXAMPLE_PROPERTY,
    NAMESPACE,
    TABLE,
    characters,
    drop_example_table,
    lakehouse,
    refresh_table_credentials,
    schema_reference,
):
    lakehouse.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_reference(NAMESPACE)}")
    # Replaces only a table that an earlier run of this example created.
    drop_example_table(lakehouse, NAMESPACE, TABLE, EXAMPLE)
    lakehouse.execute(f"""
        CREATE TABLE {characters} (
            id INTEGER, name VARCHAR, status VARCHAR, species VARCHAR,
            gender VARCHAR, origin VARCHAR, location VARCHAR
        ) WITH ('{EXAMPLE_PROPERTY}' = '{EXAMPLE}')
    """)
    refresh_table_credentials(lakehouse, NAMESPACE, TABLE)

    api = "https://rickandmortyapi.com/api/character"
    count = lakehouse.sql(f"SELECT info.count FROM read_json('{api}')").fetchall()[0][0]
    everyone = f"{api}/" + ",".join(str(n) for n in range(1, count + 1))
    lakehouse.execute(f"""
        INSERT INTO {characters}
        SELECT id, name, status, species, gender, origin.name, location.name
        FROM read_json('{everyone}')
    """)
    created = True
    return (created,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Look inside
    Every write creates a snapshot. A snapshot lists manifests, and the manifests
    list the data files.
    """)


@app.cell
def _(created, files, lakehouse, mo, snapshots):
    assert created
    mo.vstack([snapshots(lakehouse), files(lakehouse)])


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Delete Jerry Smith
    DuckDB does not rewrite the Parquet file. In this format version 2 table it
    writes a small delete file that marks the deleted rows, and commits a new
    snapshot. A version 3 table gets a deletion vector instead (see example 04).
    """)


@app.cell
def _(characters, created, lakehouse):
    assert created
    lakehouse.execute(f"DELETE FROM {characters} WHERE name = 'Jerry Smith'")
    deleted = True
    return (deleted,)


@app.cell
def _(deleted, files, lakehouse, mo, snapshots):
    assert deleted
    mo.vstack([snapshots(lakehouse), files(lakehouse)])


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Time travel
    The first snapshot still exists, and so does its Parquet file. Jerry is gone
    from the table, but not from the bucket.
    """)


@app.cell
def _(characters, deleted, lakehouse, plain_table):
    assert deleted
    first_snapshot = lakehouse.sql(
        f"SELECT min_by(snapshot_id, sequence_number) FROM iceberg_snapshots({characters})"
    ).fetchall()[0][0]
    plain_table(lakehouse.sql(f"""
        SELECT id, name, origin
        FROM {characters} AT (VERSION => {first_snapshot})
        WHERE name = 'Jerry Smith'
    """).df())
    return (first_snapshot,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. Make the delete permanent
    Three steps: rewrite the data without the deleted rows, expire the old
    snapshots, and remove the files that no snapshot uses any more. DuckDB cannot
    expire snapshots yet, so these steps use PyIceberg on the same table.
    """)


@app.cell
def _(NAMESPACE, TABLE, connect, first_snapshot):
    assert first_snapshot
    catalog = connect()
    table = catalog.load_table((*NAMESPACE, TABLE))

    # 1. Rewrite: a new Parquet file with only the rows that still exist.
    table.overwrite(table.scan().to_arrow())
    rewritten = True
    return rewritten, table


@app.cell
def _(datetime, lakehouse, rewritten, snapshots, table, timezone):
    assert rewritten
    # 2. Expire every snapshot except the current one.
    table.maintenance.expire_snapshots().older_than(datetime.now(timezone.utc)).commit()
    expired = True
    snapshots(lakehouse)
    return (expired,)


@app.cell
def _(expired, mo, objects, short, table):
    assert expired
    # 3. Remove the files that no snapshot refers to.
    in_use = {table.metadata_location} | {log.metadata_file for log in table.metadata.metadata_log}
    for snapshot in table.snapshots():
        in_use.add(snapshot.manifest_list)
        for manifest in snapshot.manifests(table.io):
            in_use.add(manifest.manifest_path)
            in_use.update(entry.data_file.file_path for entry in manifest.fetch_manifest_entry(table.io))

    removed = sorted(objects(table) - in_use)
    for path in removed:
        table.io.delete(path)
    mo.md("Removed:\n\n" + "\n".join(f"- `{short(path)}`" for path in removed))
    return (removed,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. Check
    Time travel to the first snapshot fails, and no Parquet file in the bucket
    still holds Jerry.
    """)


@app.cell
def _(characters, first_snapshot, lakehouse, mo, objects, removed, table):
    import duckdb as _duckdb

    assert removed is not None
    try:
        lakehouse.sql(f"SELECT count(*) FROM {characters} AT (VERSION => {first_snapshot})").fetchall()
        time_travel = "still possible"
    except _duckdb.Error:
        time_travel = "no longer possible"

    data_files = [path for path in objects(table) if path.endswith(".parquet") and "-deletes" not in path]
    jerry = lakehouse.sql(
        f"SELECT count(*) FROM read_parquet({data_files}) WHERE name = 'Jerry Smith'"
    ).fetchall()[0][0]
    mo.md(f"Time travel to the first snapshot: **{time_travel}**. Jerry Smith in the data files: **{jerry}**.")


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. Read it as a consumer
    Any engine that speaks Iceberg can read this table: DuckDB here, or Polars,
    PyIceberg, Spark or Snowflake. It fetches only the files it needs.
    """)


@app.cell
def _(characters, lakehouse, plain_table, removed):
    assert removed is not None
    plain_table(lakehouse.sql(f"""
        SELECT species, count(*) AS characters
        FROM {characters}
        GROUP BY species
        ORDER BY characters DESC
        LIMIT 5
    """).df())


if __name__ == "__main__":
    app.run()
