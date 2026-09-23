import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="06 · Write an AI-ready flights product", sql_output="pandas")


@app.cell
def _():
    import marimo as mo

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import connect_duckdb
    from user_portal.notebook.flights import (
        datasets,
        generate_flights,
        load_semantics,
        publish_flights,
        quality_report,
        require_quality,
    )

    NAMESPACE = ["ai_flights"]
    FLIGHT_COUNT = 12000
    return (
        FLIGHT_COUNT,
        NAMESPACE,
        connect_duckdb,
        datasets,
        generate_flights,
        load_semantics,
        mo,
        publish_flights,
        quality_report,
        require_quality,
        plain_table,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Publish an AI-ready flights data product
    **Example 6 of 7 · Native DuckDB → Iceberg → Polaris**

    A useful data product combines **data, meaning, quality evidence and access**.
    This notebook generates **12,000 synthetic flight instances** plus airports,
    carriers, aircraft, routes and runways. DuckDB generates and writes every row
    using SQL. No real flight records or passenger data are used.

    The [Apache Ossie flights ontology](https://github.com/apache/ossie/blob/main/examples/flights.yaml)
    supplies the concepts, relationships, units and physical field mappings.
    `flights.yaml` is the unchanged upstream model beside this notebook;
    `flights.product.yaml` records this demo's additional policies and SQL checks.

    Run top to bottom with a **writer role** in the selected database. Reruns replace
    only the six tables owned by this example in `ai_flights`; a conflicting table
    stops publication before any replacement. Each table is committed separately:
    finish this notebook before opening example 7, and rerun after an interrupted
    publication. Change `NAMESPACE` in both notebooks to use another namespace.
    """)


@app.cell
def _(load_semantics, mo):
    model, product, fingerprints = load_semantics(mo.notebook_dir())
    mo.md(f"""
    ## 1. Read the meaning before generating rows
    **{model["name"]}** - {model["description"]}

    **Grain:** {product["grain"]}

    **Time:** {product["time_convention"]}

    **Missing values:** {product["null_policy"]}

    **Scope:** {product["ontology"]["scope"]}
    """)
    return fingerprints, model, product


@app.cell
def _(NAMESPACE, datasets, mo, model, plain_table):
    plain_table(
        [
            {
                "dataset": _d["name"],
                "Ossie source": _d["source"],
                "published table": "lakehouse."
                + ".".join(NAMESPACE)
                + "."
                + _d["source"].split(".")[-1].lower(),
                "mapped fields": len(_d["fields"]),
            }
            for _d in datasets(model)
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Generate a reproducible product in DuckDB
    The default covers **January 1–30, 2026**, with 400 scheduled instances per day,
    8 fictional airports, 3 carriers, 120 aircraft, 56 directed routes and 16 runways.
    Schedules and delays are deterministic. Every mapped field is present, along
    with a `synthetic` flag. Distances are in miles, delays in minutes and timestamps
    represent UTC. Cancellations, diversions and early arrivals exercise the semantics.
    """)


@app.cell
def _(NAMESPACE, connect_duckdb):
    lakehouse = connect_duckdb(NAMESPACE, "flights", read_only=False, missing_ok=True)
    return (lakehouse,)


@app.cell
def _(FLIGHT_COUNT, generate_flights, lakehouse, model):
    generate_flights(lakehouse, model, FLIGHT_COUNT)
    generated = True
    return (generated,)


@app.cell
def _(generated, lakehouse, mo, plain_table):
    assert generated
    preview = mo.sql("SELECT * FROM FLIGHT ORDER BY id LIMIT 12", engine=lakehouse, output=False)
    plain_table(preview)
    return (preview,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Turn selected semantic rules into quality evidence
    These executable checks cover identity, join integrity, coordinates, time
    ordering, cancellation nulls and units implied by timestamp arithmetic.
    They implement selected rules explicitly; this notebook does not evaluate
    Ossie's constraint language. Publication stops if a check fails.
    """)


@app.cell
def _(generated, lakehouse, mo, product, quality_report, require_quality, plain_table):
    assert generated
    checks = quality_report(lakehouse, product)
    require_quality(checks)
    plain_table(checks)
    return (checks,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. Publish the data and record its semantic version
    DuckDB creates Iceberg v2 tables and inserts from its local SQL relations.
    Polaris grants access with your identity and vends credentials scoped to each
    table. Every table records hashes of the ontology and demo contract, so the
    reader can detect when its local definitions no longer match the published data.
    The YAML remains in the shared notebook folder; hashes identify it, not distribute it.
    """)


@app.cell
def _(NAMESPACE, checks, fingerprints, lakehouse, mo, model, publish_flights, plain_table):
    assert all(_check["violations"] == 0 for _check in checks)
    published = publish_flights(lakehouse, model, NAMESPACE, fingerprints)
    plain_table(published)
    return (published,)


@app.cell(hide_code=True)
def _(mo, published):
    _flights = next(_row["rows"] for _row in published if _row["dataset"] == "FLIGHT")
    mo.md(f"""
    ## Ready to explain and query
    Published **{_flights:,} flight instances** and five supporting tables.

    Open **07 · Read an AI-ready flights product** in the same database. It follows
    business questions through the ontology to SQL, checks join cardinality and
    explains why a punctuality metric needs a precise denominator.

    For the talk: portable semantics help an AI consumer discover meaning, while
    quality checks supply evidence and Polaris enforces access. These are building
    blocks for trustworthy answers; an ontology alone cannot guarantee them.
    """)


if __name__ == "__main__":
    app.run()
