import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="09 · Write an AI-ready grid forecast with Polars")


@app.cell
def _():
    import marimo as mo
    import polars as pl
    from pyiceberg.exceptions import ForbiddenError

    from user_portal.notebook.connection import connect
    from user_portal.notebook.display import plain_table
    from user_portal.notebook.grid_forecast import (
        collect_product,
        generate_grid_forecast,
        load_contract,
        publish_grid_forecast,
        quality_report,
        require_quality,
        semantic_model,
    )
    from user_portal.notebook.semantic import SemanticModels

    NAMESPACE = ["ai_grid_forecast"]
    MODEL_NAME = "mv_load_forecast"
    return (
        ForbiddenError,
        MODEL_NAME,
        NAMESPACE,
        SemanticModels,
        collect_product,
        connect,
        generate_grid_forecast,
        load_contract,
        mo,
        pl,
        plain_table,
        publish_grid_forecast,
        quality_report,
        require_quality,
        semantic_model,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Publish an AI-ready grid forecast with Polars
    **Example 9 of 11 · Polars 2 → Iceberg → Polaris**

    Distribution network architects decide where the medium-voltage (MV) grid needs investment.
    They open a precomputed congestion overview for a few substations, then analyse the area:
    the load forecast per asset and year, the large customers that are connected or still to come,
    and what the spreading models for EVs, heat pumps and solar add to each peak.

    This notebook builds that product for **6 fictional substations, 48 feeders and 288 assets**,
    forecast for 2026 to 2040. **Polars 2** generates every row as a lazy query, derives the
    congestion outlook, checks the result with SQL and writes the tables with its own Iceberg sink.
    `grid_forecast.product.yaml` beside this notebook is the product contract: it defines the
    tables, what every column means, the joins, the metrics and the quality checks. At the end,
    that meaning is stored in Polaris as a **semantic model**, next to the tables.

    Run top to bottom with a **writer role** in the selected database. A rerun overwrites only the
    eight tables this example owns in `ai_grid_forecast`, so they keep their identity, snapshot
    history and data shares; a table owned by anything else stops the run before any write.
    Finish this notebook before opening example 10. Change `NAMESPACE` in both notebooks to use
    another namespace.
    """)


@app.cell
def _(load_contract, mo):
    product = load_contract(mo.notebook_dir())
    mo.md(f"""
    ## 1. Read the meaning before generating rows
    **{product["name"]}** · {product["description"]}

    **Grain:** {product["grain"]}

    **Time:** {product["time_convention"]}

    **Missing values:** {product["null_policy"]}

    **Scope:** {product["scope"]}
    """)
    return (product,)


@app.cell
def _(NAMESPACE, plain_table, product):
    plain_table(
        [
            {
                "dataset": _name,
                "Iceberg table": ".".join(["lakehouse", *NAMESPACE, _d["table"]]),
                "key": ", ".join(_d["primary_key"]),
                "partitioned by": ", ".join(_d.get("partition_by", [])) or "-",
                "fields": len(_d["fields"]),
            }
            for _name, _d in product["datasets"].items()
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Generate the product as lazy Polars queries
    Every dataset starts as a `LazyFrame`: a query plan, not rows. The forecast crosses each asset
    with the years and both directions, then adds three parts: the **baseline** peak of 2025, the
    growth that five **spreading models** assign to the asset, and the **large customers** connected
    to it. Power is computed in whole kW, so every sum is exact in whatever order the streaming
    engine adds rows; MW and utilization are derived once, at the end. Nothing random is involved.

    Polars 2 runs lazy queries on its **streaming engine** by default and no longer promises row
    order after joins and group-bys, so every table is sorted by its key before it is written.
    `pl.collect_all` runs all eight plans together, sharing the work they have in common.
    """)


@app.cell
def _(collect_product, generate_grid_forecast, mo, product):
    frames = generate_grid_forecast(product)
    tables = collect_product(frames)
    mo.accordion({"Optimized query plan of LOAD_FORECAST": mo.plain_text(frames["LOAD_FORECAST"].explain())})
    return frames, tables


@app.cell
def _(mo, plain_table, tables):
    mo.vstack(
        [
            plain_table([{"dataset": _name, "rows": _t.height, "columns": _t.width} for _name, _t in tables.items()],
                        label="Generated tables"),
            plain_table(tables["SUBSTATION"], label="SUBSTATION"),
            plain_table(tables["LOAD_FORECAST"].head(12), label="LOAD_FORECAST · first 12 rows"),
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Derive the congestion outlook with expressions
    The congestion overview is precomputed, so an architect sees it the moment they open a
    substation. `derive_congestion` groups the forecast per asset and direction and computes, in one
    pass: the **first year** utilization reaches 100 percent (a filter inside the aggregation), the
    **peak** and the earliest year it occurs (`sort_by(...).first()`), the number of congested years
    and the **overload** in MW. A `when/then` chain turns the peak into a **severity**.

    The summary below follows the joins the contract defines, from congestion to asset, feeder and
    substation.
    """)


@app.cell
def _(pl, plain_table, tables):
    _points = pl.col("first_congestion_year").is_not_null()
    congestion_overview = (
        tables["CONGESTION"].lazy()
        .join(tables["ASSET"].lazy().select("asset_id", "feeder_id"), on="asset_id")
        .join(tables["FEEDER"].lazy().select("feeder_id", "substation_id"), on="feeder_id")
        .join(tables["SUBSTATION"].lazy().select("substation_id", "name"), on="substation_id")
        .group_by("substation_id", "name")
        .agg(
            congestion_points=_points.sum(),
            offtake=(_points & (pl.col("direction") == "offtake")).sum(),
            feed_in=(_points & (pl.col("direction") == "feed_in")).sum(),
            first_year=pl.col("first_congestion_year").min(),
            high_or_critical=pl.col("severity").is_in(["high", "critical"]).sum(),
        )
        .sort("substation_id")
        .collect()
    )
    plain_table(congestion_overview, label="Future congestion points per substation, 2026–2040")
    return (congestion_overview,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. Turn the contract into quality evidence
    The contract's checks are plain SQL, and Polars runs them itself with `pl.SQLContext`: one
    table per dataset, named like the dataset. They check completeness, keys, references, MV scope,
    that every peak adds up from its parts and matches its utilization, that the spreading models
    and customers explain those parts, and that the congestion outlook agrees with the forecast
    when recomputed in SQL. Publication stops if a check fails.
    """)


@app.cell
def _(plain_table, product, quality_report, require_quality, tables):
    checks = quality_report(tables, product)
    require_quality(checks)
    plain_table(checks)
    return (checks,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. Publish the data with Polars' Iceberg sink
    PyIceberg creates each table from the contract: column types, a **doc for every column**, the
    table comment and, for the forecast and contributions, **partitions by year**. Then
    `LazyFrame.sink_iceberg` writes the rows itself and commits them through the catalog. Polaris
    checks your identity and vends storage credentials scoped to each table.

    The table properties record who produced the table, when and with which quality result, and
    which tables the congestion outlook was derived from. The portal's Catalog shows them under
    **Produced by**.
    """)


@app.cell
def _(ForbiddenError, NAMESPACE, checks, connect, mo, plain_table, product, publish_grid_forecast, tables):
    try:
        published = publish_grid_forecast(connect(), tables, product, NAMESPACE, checks)
    except ForbiddenError:
        mo.stop(
            True,
            mo.callout(
                "Your user does not have write permissions for this database. A writer can publish the product; "
                "readers can then use example 10.",
                kind="warn",
            ),
        )
    plain_table(published)
    return (published,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. Publish the meaning next to the data
    Polaris stores an [Apache Ossie](https://github.com/apache/ossie) **semantic model** in the
    namespace, beside the tables it describes. Every part comes from the contract: each dataset
    points at its Iceberg table and names its key, every field has a description, a **label** that
    names its concept and synonyms for AI agents, the **relationships** state the joins and the
    **metrics** carry the agreed SQL. The AI context holds the grain, the horizon, the
    missing-value policy and the guidance, such as never adding up peaks of different assets.

    Polaris authorizes the model like a table: team readers can read it, writers can change it.
    A rerun replaces the model; a teammate's change in the meantime is refused instead of
    silently overwritten.
    """)


@app.cell
def _(MODEL_NAME, NAMESPACE, SemanticModels, plain_table, product, published, semantic_model):
    assert published
    grid_model = semantic_model(product, NAMESPACE)
    with SemanticModels.connect(NAMESPACE) as _models:
        model_action, model_version = _models.publish(MODEL_NAME, grid_model)
    plain_table(
        [
            {
                "semantic model": ".".join([*NAMESPACE, MODEL_NAME]),
                "result": model_action,
                "entity version": model_version,
                "datasets": len(grid_model["datasets"]),
                "relationships": len(grid_model["relationships"]),
                "metrics": len(grid_model["metrics"]),
            }
        ]
    )
    return grid_model, model_action, model_version


@app.cell(hide_code=True)
def _(congestion_overview, mo, model_action, published):
    _forecast = next(_row["rows"] for _row in published if _row["dataset"] == "LOAD_FORECAST")
    mo.md(f"""
    ## Ready to analyse
    Published **{_forecast:,} forecast rows**, seven supporting tables with
    **{congestion_overview["congestion_points"].sum()} future congestion points**, and the
    `mv_load_forecast` semantic model ({model_action}).

    Open **10 · Read an AI-ready grid forecast with Polars** in the same database, with any role.
    It reads the semantic model from Polaris, scans the tables lazily, checks the data against the
    model and analyses an area of substations with the stored joins and metrics. The portal's
    **Catalog** shows the column docs, the model in `ai_grid_forecast` and the quality evidence;
    AI agents read the model with the MCP tool `describe_semantic_model`.
    """)


if __name__ == "__main__":
    app.run()
