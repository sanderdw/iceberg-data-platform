import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="10 · Read an AI-ready grid forecast with Polars")


@app.cell
def _():
    # The area to analyse: substation codes from the overview below.
    SUBSTATIONS = ["SS-ALD", "SS-DUN"]
    return (SUBSTATIONS,)


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt
    import polars as pl

    from user_portal.notebook.connection import connect
    from user_portal.notebook.display import plain_table
    from user_portal.notebook.semantic import (
        SemanticModels,
        joins,
        metric_sql,
        model_check_queries,
        scan_datasets,
    )

    NAMESPACE = ["ai_grid_forecast"]
    MODEL_NAME = "mv_load_forecast"
    return (
        MODEL_NAME,
        NAMESPACE,
        SemanticModels,
        connect,
        joins,
        metric_sql,
        mo,
        model_check_queries,
        pl,
        plain_table,
        plt,
        scan_datasets,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Find and explain congestion in an AI-ready grid forecast
    **Example 10 of 10 · Semantic model and Iceberg tables from Polaris, read with Polars**

    Run **09 · Write an AI-ready grid forecast with Polars** first in the same database.
    This notebook needs no local files: everything comes from Polaris, with your own permissions.
    The **semantic model** says what the tables mean; the **Iceberg tables** hold the rows, and
    Polars scans them lazily. No AI service, API key or external data download is needed.

    The story follows a distribution network architect: **discover the meaning → verify it → open
    the congestion overview → analyse an area → explain a peak**. Set `SUBSTATIONS` in the first
    cell to choose the area. Every result below is synthetic.
    """)


@app.cell
def _(MODEL_NAME, NAMESPACE, SemanticModels):
    with SemanticModels.connect(NAMESPACE) as _models:
        _found = _models.load(MODEL_NAME)
    if _found is None:
        raise RuntimeError("There is no MV load forecast semantic model in this namespace yet. Run notebook 09 first.")
    grid_model, model_version = _found
    return grid_model, model_version


@app.cell(hide_code=True)
def _(MODEL_NAME, NAMESPACE, grid_model, mo, model_version):
    mo.md(f"""
    ## 1. Discover the meaning
    **{grid_model["name"]}** · semantic model `{".".join([*NAMESPACE, MODEL_NAME])}`,
    entity version {model_version}

    {grid_model["description"]}

    The model maps each dataset to an Iceberg table, names its key, states the joins between
    datasets and carries the agreed metrics. Its AI context:

    {"  \n".join(grid_model["ai_context"]["instructions"].splitlines())}
    """)


@app.cell
def _(grid_model, mo, plain_table):
    mo.vstack(
        [
            plain_table(
                [
                    {"dataset": _d["name"], "Iceberg table": _d["source"],
                     "primary key": ", ".join(_d["primary_key"]), "fields": len(_d["fields"])}
                    for _d in grid_model["datasets"]
                ],
                label="Datasets",
            ),
            plain_table(
                [
                    {"relationship": _r["name"], "join": f"{_r['from']}.{', '.join(_r['from_columns'])} → "
                     f"{_r['to']}.{', '.join(_r['to_columns'])}"}
                    for _r in grid_model["relationships"]
                ],
                label="Relationships (many to one)",
            ),
            plain_table(
                [
                    {"metric": _m["name"], "SQL": _m["expression"]["dialects"][0]["expression"],
                     "definition": _m["description"]}
                    for _m in grid_model["metrics"]
                ],
                label="Metrics",
            ),
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Scan the tables lazily
    `scan_datasets` gives one `pl.scan_iceberg` per dataset, named like the dataset, over the table
    the model points at. A scan reads nothing yet. Polars pushes filters and column selections into
    it: the plan below reads only the 2035 partition of the forecast and only two of its columns.
    The product is small, so the notebook then reads every table once with `pl.collect_all`.
    """)


@app.cell
def _(connect, grid_model, mo, pl, scan_datasets):
    scans = scan_datasets(connect(), grid_model)
    _plan = scans["LOAD_FORECAST"].filter(pl.col("year") == 2035).select("asset_id", "utilization_pct")
    frames = dict(zip(scans, pl.collect_all(scans.values()), strict=True))
    sql = pl.SQLContext(frames, eager=True)
    mo.plain_text(_plan.explain())
    return frames, sql


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. Verify the meaning against the data
    A model is only useful if the data honors it. These checks come from the model itself: every
    primary key must be unique, and every relationship must find its target row. They are plain
    SQL, run here by Polars. The notebook stops if one fails.
    """)


@app.cell
def _(grid_model, model_check_queries, plain_table, sql):
    checks = [{"check": _label, "violations": sql.execute(_query).item()}
              for _label, _query in model_check_queries(grid_model)]
    _failed = [_c["check"] for _c in checks if _c["violations"]]
    if _failed:
        raise ValueError("The data does not match its semantic model: " + "; ".join(_failed))
    plain_table(checks)
    return (checks,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. “How many future congestion points does each substation have?”
    The architect's first view. The metrics `congestion_points` and `earliest_congestion_year`
    come from the model, and so does the path from an outlook to its substation: follow
    `congestion_asset`, `asset_feeder` and `feeder_substation`. All three are many to one, so every
    outlook stays one row. Polars runs the SQL assembled from the stored model.
    """)


@app.cell
def _(checks, grid_model, joins, metric_sql, mo):
    assert all(_c["violations"] == 0 for _c in checks)
    overview_sql = f"""
    SELECT SUBSTATION.substation_id, SUBSTATION.name, CONGESTION.direction,
           {metric_sql(grid_model, "congestion_points")} AS congestion_points,
           {metric_sql(grid_model, "earliest_congestion_year")} AS earliest_congestion_year
    FROM CONGESTION
    {joins(grid_model, "congestion_asset", "asset_feeder", "feeder_substation")}
    GROUP BY SUBSTATION.substation_id, SUBSTATION.name, CONGESTION.direction
    ORDER BY SUBSTATION.substation_id, CONGESTION.direction DESC
    """
    mo.md(f"```sql{overview_sql}```")
    return (overview_sql,)


@app.cell
def _(overview_sql, plain_table, sql):
    congestion_overview = sql.execute(overview_sql)
    plain_table(congestion_overview)
    return (congestion_overview,)


@app.cell(hide_code=True)
def _(SUBSTATIONS, mo):
    mo.md(f"""
    ## 5. Analyse the area: {", ".join(SUBSTATIONS)}
    The same question, but per year inside the area: how many assets are congested, and how high
    does the worst one get? `congested_assets` counts each asset once and `peak_utilization_pct`
    takes the highest peak; neither adds up peaks, which never occur at the same moment.
    """)


@app.cell
def _(SUBSTATIONS, grid_model, joins, metric_sql, sql):
    _area = ", ".join(f"'{_s}'" for _s in SUBSTATIONS)
    area_by_year = sql.execute(f"""
        SELECT LOAD_FORECAST.year, LOAD_FORECAST.direction,
               {metric_sql(grid_model, "congested_assets")} AS congested_assets,
               {metric_sql(grid_model, "peak_utilization_pct")} AS peak_utilization_pct
        FROM LOAD_FORECAST
        {joins(grid_model, "forecast_asset", "asset_feeder", "feeder_substation")}
        WHERE SUBSTATION.substation_id IN ({_area})
        GROUP BY LOAD_FORECAST.year, LOAD_FORECAST.direction
        ORDER BY LOAD_FORECAST.year, LOAD_FORECAST.direction DESC
    """)
    return (area_by_year,)


@app.cell
def _(area_by_year, mo, pl, plt):
    _figure, _axes = plt.subplots(figsize=(9, 3.5), layout="constrained")
    for _direction, _color in (("offtake", "#d71921"), ("feed_in", "#5b5b5b")):
        _rows = area_by_year.filter(pl.col("direction") == _direction)
        _axes.plot(_rows["year"], _rows["congested_assets"], marker="o", color=_color, label=_direction)
    _axes.set(xlabel="Forecast year", ylabel="Congested assets")
    _axes.legend(frameon=False)
    _chart = mo.as_html(_figure)
    plt.close(_figure)
    mo.vstack([_chart])


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### The congestion map
    Every asset in the area at its location, colored by the severity of its worst direction and
    labeled with the year congestion starts, one map per substation. Black squares are the substations.
    """)


@app.cell
def _(SUBSTATIONS, frames, mo, pl, plt):
    _order = ["none", "moderate", "high", "critical"]
    _colors = {"none": "#c8c8c8", "moderate": "#f2b705", "high": "#e8590c", "critical": "#d71921"}
    area_assets = (
        frames["ASSET"].lazy()
        .join(frames["FEEDER"].lazy().select("feeder_id", "substation_id"), on="feeder_id")
        .filter(pl.col("substation_id").is_in(SUBSTATIONS))
        .join(
            frames["CONGESTION"].lazy()
            .with_columns(rank=pl.col("severity").replace_strict(_order, list(range(len(_order)))))
            .sort("rank", "first_congestion_year", descending=[True, False], nulls_last=True)
            .group_by("asset_id", maintain_order=True)
            .first(),
            on="asset_id",
        )
        .sort("asset_id")
        .collect()
    )
    _stations = frames["SUBSTATION"].filter(pl.col("substation_id").is_in(SUBSTATIONS)).sort("substation_id")
    # One map per substation: substations lie far apart, their assets within a few kilometers.
    _figure, _maps = plt.subplots(1, _stations.height, figsize=(4.5 * _stations.height, 4.8), layout="constrained",
                                  squeeze=False)
    for _axes, _station in zip(_maps[0], _stations.iter_rows(named=True), strict=True):
        _here = area_assets.filter(pl.col("substation_id") == _station["substation_id"])
        for _severity in _order:
            _rows = _here.filter(pl.col("severity") == _severity)
            _axes.scatter(_rows["longitude"], _rows["latitude"], s=28, color=_colors[_severity], label=_severity)
        for _row in _here.filter(pl.col("first_congestion_year").is_not_null()).iter_rows(named=True):
            _axes.annotate(str(_row["first_congestion_year"]), (_row["longitude"], _row["latitude"]),
                           fontsize=6, xytext=(3, 3), textcoords="offset points")
        _axes.scatter([_station["longitude"]], [_station["latitude"]], marker="s", s=60, color="black",
                      label="substation")
        _axes.set(title=_station["name"], xlabel="Longitude", ylabel="Latitude", aspect=1.6)
    _maps[0][-1].legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1))
    _chart = mo.as_html(_figure)
    plt.close(_figure)
    mo.vstack([_chart])
    return (area_assets,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### The worst assets, year by year
    The ten assets in the area with the highest peak, as a pivot of their utilization in selected
    years. Polars' `pivot` turns the years into columns.
    """)


@app.cell
def _(area_assets, frames, pl, plain_table):
    _worst = area_assets.sort("peak_utilization_pct", "asset_id", descending=[True, False]).head(10)
    worst_assets = (
        frames["LOAD_FORECAST"]
        .join(_worst.select("asset_id", "name", "asset_type", "direction"), on=["asset_id", "direction"])
        .filter(pl.col("year").is_in([2026, 2030, 2035, 2040]))
        .pivot("year", index=["asset_id", "name", "asset_type", "direction"], values="utilization_pct",
               sort_columns=True)
        .sort("2040", "asset_id", descending=[True, False])
    )
    plain_table(worst_assets, label="Utilization in percent of rated capacity")
    return (worst_assets,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Large customers, connected and to come
    Connected customers count with their contracted capacity; future customers with their
    requested capacity from their planned year. The model asks to report the two groups apart.
    """)


@app.cell
def _(SUBSTATIONS, frames, pl, plain_table):
    area_customers = (
        frames["LARGE_CUSTOMER"].lazy()
        .join(frames["ASSET"].lazy().select("asset_id", "feeder_id"), on="asset_id")
        .join(frames["FEEDER"].lazy().select("feeder_id", "substation_id"), on="feeder_id")
        .filter(pl.col("substation_id").is_in(SUBSTATIONS))
        .select(
            "customer_id", "name", "sector", "asset_id", "status",
            offtake_mw=pl.coalesce("contracted_offtake_mw", "requested_offtake_mw"),
            feed_in_mw=pl.coalesce("contracted_feed_in_mw", "requested_feed_in_mw"),
            planned_year="planned_year",
        )
        .sort("status", "planned_year", "customer_id", nulls_last=True)
        .collect()
    )
    plain_table(area_customers)
    return (area_customers,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. Explain one peak
    Why does the worst asset in the area become congested? Its peak is the baseline, plus what each
    spreading model adds, plus its large customers. `contribution_forecast` joins every
    contribution to its own forecast row on all three keys; within one asset, year and direction
    the parts add up exactly. The line is the asset's rated capacity.
    """)


@app.cell
def _(area_assets, frames, mo, pl, plt):
    _worst = area_assets.sort("peak_utilization_pct", "asset_id", descending=[True, False]).row(0, named=True)
    _key = (pl.col("asset_id") == _worst["asset_id"]) & (pl.col("direction") == _worst["direction"])
    _forecast = frames["LOAD_FORECAST"].filter(_key).sort("year")
    peak_parts = (
        frames["SPREADING_CONTRIBUTION"].filter(_key)
        .join(frames["SPREADING_MODEL"].select("model_id", "name"), on="model_id")
        .pivot("name", index="year", values="contribution_mw", sort_columns=True)
        .sort("year")
    )
    _figure, _axes = plt.subplots(figsize=(10, 4), layout="constrained")
    _bottom = [0.0] * _forecast.height
    _parts = [("baseline", _forecast["baseline_mw"]), ("large customers", _forecast["customer_mw"]),
              *((_name, peak_parts[_name]) for _name in peak_parts.columns if _name != "year")]
    _palette = ["#5b5b5b", "#1a1a1a", "#d71921", "#e8590c", "#f2b705", "#7a7a7a", "#a8a8a8"]
    for (_label, _values), _color in zip(_parts, _palette, strict=False):
        _values = _values.to_list()
        _base = [_b if _v >= 0 else 0 for _b, _v in zip(_bottom, _values, strict=True)]
        _axes.bar(_forecast["year"], _values, bottom=_base, color=_color, label=_label)
        _bottom = [_b + max(_v, 0) for _b, _v in zip(_bottom, _values, strict=True)]
    _axes.axhline(_worst["rated_mw"], color="black", linewidth=1, linestyle="--", label="rated capacity")
    _axes.set(xlabel="Forecast year", ylabel="MW",
              title=f"{_worst['name']} · {_worst['direction']} · congested from {_worst['first_congestion_year']}")
    _axes.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1))
    _chart = mo.as_html(_figure)
    plt.close(_figure)
    mo.vstack([_chart])
    return (peak_parts,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 7. One definition, two engines, and a sum to avoid
    The stored metric `congested_assets` is SQL. The same count with Polars expressions must give
    the same answer, or the model and the code disagree. Below that, the pitfall the model warns
    about: adding up the peaks of a feeder's assets gives a number no asset ever carries.
    """)


@app.cell
def _(SUBSTATIONS, area_by_year, frames, pl):
    _in_area = (
        frames["LOAD_FORECAST"].lazy()
        .join(frames["ASSET"].lazy().select("asset_id", "feeder_id", "rated_mw"), on="asset_id")
        .join(frames["FEEDER"].lazy().select("feeder_id", "substation_id"), on="feeder_id")
        .filter(pl.col("substation_id").is_in(SUBSTATIONS))
    )
    _by_expressions = _in_area.group_by("year", "direction").agg(
        polars_expressions=pl.col("asset_id").filter(pl.col("utilization_pct") >= 100).n_unique()
    )
    engines_agree = (
        area_by_year.lazy()
        .select("year", "direction", stored_sql="congested_assets")
        .join(_by_expressions, on=["year", "direction"])
        .with_columns(agree=pl.col("stored_sql") == pl.col("polars_expressions"))
        .sort("year", "direction", descending=[False, True])
        .collect()
    )
    if not engines_agree["agree"].all():
        raise ValueError("The stored metric and the Polars expressions disagree.")
    peak_sum_pitfall = (
        _in_area.filter((pl.col("year") == 2040) & (pl.col("direction") == "offtake"))
        .group_by("feeder_id")
        .agg(
            added_up_peaks_mw=pl.col("peak_mw").sum().round(2),
            largest_rating_mw=pl.col("rated_mw").max(),
            peak_utilization_pct=pl.col("utilization_pct").max(),
            congested_assets=(pl.col("utilization_pct") >= 100).sum(),
        )
        .sort("feeder_id")
        .collect()
    )
    return engines_agree, peak_sum_pitfall


@app.cell
def _(engines_agree, mo, peak_sum_pitfall, pl, plain_table):
    mo.vstack(
        [
            mo.callout(f"congested_assets agrees in all {engines_agree.height} years and directions.", kind="success"),
            plain_table(engines_agree.filter(pl.col("year").is_in([2030, 2035, 2040])),
                        label="congested_assets in the area: stored SQL and Polars expressions"),
            plain_table(peak_sum_pitfall, label="Offtake in 2040 per feeder: added-up peaks against the largest rating"),
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 8. The same meaning for an AI agent
    An agent with the platform's MCP server calls `describe_semantic_model` and gets this model:
    datasets, relationships, metrics and AI context, with the user's own permissions. Governed
    queries (`query_semantic_model`) run the stored metrics along the stored joins only.

    **Example prompt:** “Use the mv_load_forecast semantic model in ai_grid_forecast. Which
    substations get the most offtake congestion points before 2035, and what drives the worst
    asset's peak? State the year window, the direction and the 100 percent threshold, and label
    the result synthetic.”

    This is the **baseline** forecast. Architects who disagree with it adjust the spreading models
    in their investment proposals; those adjustments are not in the data, so say so whenever an
    answer depends on spreading assumptions. Reconnect after another publication to see fresh
    snapshots.
    """)


if __name__ == "__main__":
    app.run()
