import marimo

__generated_with = "0.25.0"
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
        semantic_model,
    )
    from user_portal.notebook.semantic import SemanticModels

    NAMESPACE = ["ai_flights"]
    MODEL_NAME = "flights"
    FLIGHT_COUNT = 12000
    return (
        FLIGHT_COUNT,
        MODEL_NAME,
        NAMESPACE,
        SemanticModels,
        connect_duckdb,
        datasets,
        generate_flights,
        load_semantics,
        mo,
        publish_flights,
        quality_report,
        require_quality,
        plain_table,
        semantic_model,
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
    At the end, the notebook stores that meaning in Polaris as a **semantic model**,
    next to the tables. Readers then need neither file.

    Run top to bottom with a **writer role** in the selected database. Reruns replace
    only the six tables owned by this example in `ai_flights`; a conflicting table
    stops publication before any replacement. Each table is committed separately:
    finish this notebook before opening example 7, and rerun after an interrupted
    publication. Change `NAMESPACE` in both notebooks to use another namespace.
    """)


@app.cell
def _(load_semantics, mo):
    model, product = load_semantics(mo.notebook_dir())
    mo.md(f"""
    ## 1. Read the meaning before generating rows
    **{model["name"]}** - {model["description"]}

    **Grain:** {product["grain"]}

    **Time:** {product["time_convention"]}

    **Missing values:** {product["null_policy"]}

    **Scope:** {product["ontology"]["scope"]}
    """)
    return model, product


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
    ## 4. Publish the data
    DuckDB creates Iceberg v2 tables and inserts from its local SQL relations.
    Polaris grants access with your identity and vends credentials scoped to each table.
    """)


@app.cell
def _(NAMESPACE, checks, lakehouse, mo, model, publish_flights, plain_table):
    assert all(_check["violations"] == 0 for _check in checks)
    published = publish_flights(lakehouse, model, NAMESPACE)
    plain_table(published)
    return (published,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. Publish the meaning next to the data
    Polaris 1.8 stores an [Apache Ossie](https://github.com/apache/ossie) **semantic model**
    in the namespace, beside the tables it describes. This one combines `flights.yaml` with the
    product contract: each dataset points at its Iceberg table and names its primary key,
    **relationships** state the joins, **metrics** carry the agreed SQL, and the AI context holds
    the grain, time convention, missing-value policy and guidance.

    Polaris authorizes the model like a table: team readers can read it, writers can change it.
    It only stores the model and never runs a metric. A rerun replaces the model; the update
    carries the version read just before, so a teammate's change in the meantime is refused
    instead of silently overwritten.
    """)


@app.cell
def _(MODEL_NAME, NAMESPACE, SemanticModels, model, plain_table, product, published, semantic_model):
    assert published
    flights_model = semantic_model(model, product, NAMESPACE)
    with SemanticModels.connect(NAMESPACE, model["version"]) as _models:
        model_action, model_version = _models.publish(MODEL_NAME, flights_model)
    plain_table(
        [
            {
                "semantic model": ".".join([*NAMESPACE, MODEL_NAME]),
                "result": model_action,
                "entity version": model_version,
                "datasets": len(flights_model["datasets"]),
                "relationships": len(flights_model["relationships"]),
                "metrics": len(flights_model["metrics"]),
            }
        ]
    )
    return flights_model, model_action, model_version


@app.cell(hide_code=True)
def _(mo, model_action, published):
    _flights = next(_row["rows"] for _row in published if _row["dataset"] == "FLIGHT")
    mo.md(f"""
    ## Ready to explain and query
    Published **{_flights:,} flight instances**, five supporting tables and the
    `flights` semantic model ({model_action}).

    Open **07 · Read an AI-ready flights product** in the same database, with any role.
    It reads the semantic model from Polaris, checks the data against it and answers
    business questions with the stored joins and metrics. The portal's **Catalog** shows the
    model in the `ai_flights` namespace, and AI agents read it with the MCP tool
    `describe_semantic_model`.

    For the talk: portable semantics help an AI consumer discover meaning, while
    quality checks supply evidence and Polaris enforces access. These are building
    blocks for trustworthy answers; an ontology alone cannot guarantee them.
    """)


if __name__ == "__main__":
    app.run()
