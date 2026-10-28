import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="08 · Load data with dlt")


@app.cell
def _():
    import os
    import tempfile

    import dlt
    import marimo as mo
    from pyiceberg.exceptions import ForbiddenError

    from user_portal.notebook.connection import connect, dlt_destination
    from user_portal.notebook.display import plain_table

    return ForbiddenError, connect, dlt, dlt_destination, mo, os, plain_table, tempfile


@app.cell(hide_code=True)
def _(mo, os):
    mo.md(f"""
    # Load data with dlt
    **Example 8 of 11 · database `{os.environ.get("ICEBERG_DATABASE_NAME", os.environ["ICEBERG_DATABASE"])}`**

    [dlt](https://dlthub.com/docs) loads data from APIs, databases and files into Iceberg tables.
    It infers the schema, turns nested lists into child tables and keeps track of what it loaded.
    Here it loads a fictional weather API page by page into `raw_dlt_demo.stations`, with your own
    identity: your team permissions apply, and only writers can load.

    Rerunning is safe: rows merge on `station_id`, so nothing is duplicated.
    For loads on a schedule as the team's service account, ask a team administrator to set up the
    **Ingestion (dlt)** extension.
    """)


@app.cell
def _(dlt):
    def weather_api(page):
        # Stands in for an HTTP API that returns 4 stations per page and 3 pages in all.
        if page >= 3:
            return []
        return [
            {
                "station_id": number,
                "name": f"Example Station {number}",
                "city": ("Exampleville", "Sampleton", "Demoburg")[number % 3],
                "elevation_m": 2 + number * 3,
                "sensors": [
                    {"kind": "temperature", "unit": "°C"},
                    {"kind": "wind", "unit": "m/s"},
                ],
            }
            for number in range(page * 4 + 1, page * 4 + 5)
        ]

    @dlt.resource(
        name="stations",
        primary_key="station_id",
        write_disposition={"disposition": "merge", "strategy": "upsert"},
    )
    def stations():
        page = 0
        while records := weather_api(page):
            yield records
            page += 1

    return (stations,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 1. Load with dlt

    `dlt_destination` points dlt's Iceberg destination at your database. dlt's own bookkeeping
    (`_dlt_loads`, `_dlt_pipeline_state`) stays in a temporary folder, so only your tables reach the
    catalog. For a real API, replace the resource with dlt's declarative REST source:

    ```python
    from dlt.sources.rest_api import rest_api_source

    source = rest_api_source({
        "client": {"base_url": "https://api.example.com/"},
        "resources": [{"name": "stations", "primary_key": "station_id"}],
    })
    ```
    """)


@app.cell
def _(ForbiddenError, connect, dlt, dlt_destination, mo, stations, tempfile):
    identifier = ("raw_dlt_demo", "stations")
    with tempfile.TemporaryDirectory() as _local:
        _pipeline = dlt.pipeline(
            "notebook_dlt_demo",
            destination=dlt_destination(_local),
            dataset_name=identifier[0],
            pipelines_dir=f"{_local}/pipelines",
        )
        try:
            _pipeline.run(stations(), table_format="iceberg")
        except Exception as _error:
            _cause = _error
            while _cause is not None and not isinstance(_cause, ForbiddenError):
                _cause = _cause.__cause__ or _cause.__context__
            if _cause is None:
                raise
            mo.stop(
                True,
                mo.callout(
                    "Your user does not have write permissions for this database. A writer can load the example table.",
                    kind="warn",
                ),
            )
        loaded_rows = _pipeline.last_trace.last_normalize_info.row_counts
    _catalog = connect()
    stored_table = _catalog.load_table(identifier)
    sensors_table = _catalog.load_table((identifier[0], "stations__sensors"))
    load_message = (
        f"Loaded {loaded_rows.get('stations', 0)} stations and {loaded_rows.get('stations__sensors', 0)} sensors. "
        f"The table now holds {stored_table.scan().count()} stations."
    )
    return load_message, sensors_table, stored_table


@app.cell(hide_code=True)
def _(load_message, mo, plain_table, sensors_table, stored_table):
    mo.vstack(
        [
            mo.callout(load_message, kind="success"),
            mo.md("## 2. Read back the stored data"),
            plain_table(stored_table.scan(limit=12).to_arrow(), label="raw_dlt_demo.stations"),
            mo.md(
                "dlt split each station's `sensors` list into the child table `stations__sensors`. "
                "Its `_dlt_parent_id` joins to the station's `_dlt_id`, and `_dlt_load_id` names the load that wrote a row."
            ),
            plain_table(sensors_table.scan(limit=6).to_arrow(), label="raw_dlt_demo.stations__sensors"),
        ]
    )


if __name__ == "__main__":
    app.run()
