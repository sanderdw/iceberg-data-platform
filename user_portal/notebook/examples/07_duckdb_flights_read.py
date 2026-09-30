import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="07 · Read an AI-ready flights product", sql_output="pandas")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import connect_duckdb
    from user_portal.notebook.semantic import SemanticModels, bind_datasets, joins, metric_sql, model_checks

    NAMESPACE = ["ai_flights"]
    MODEL_NAME = "flights"
    return (
        MODEL_NAME,
        NAMESPACE,
        SemanticModels,
        bind_datasets,
        connect_duckdb,
        joins,
        metric_sql,
        mo,
        model_checks,
        plain_table,
        plt,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # From flight records to an AI-ready data product
    **Example 7 of 7 · Semantic model and Iceberg tables from Polaris**

    Run **06 · Write an AI-ready flights product** first in the same database.
    This notebook needs no local files: everything comes from Polaris, with your own
    permissions. The **semantic model** says what the tables mean; the **Iceberg tables**
    hold the rows. DuckDB attaches the catalog read-only and scans the tables directly.
    No AI service, API key or external data download is needed.

    The story: **discover the meaning → verify it against the data → ask a precise
    question → show the SQL and its limits**. Every result below is synthetic.
    """)


@app.cell
def _(MODEL_NAME, NAMESPACE, SemanticModels):
    with SemanticModels.connect(NAMESPACE) as _models:
        _found = _models.load(MODEL_NAME)
    if _found is None:
        raise RuntimeError("There is no flights semantic model in this namespace yet. Run notebook 06 first.")
    flights_model, model_version = _found
    return flights_model, model_version


@app.cell(hide_code=True)
def _(MODEL_NAME, NAMESPACE, flights_model, mo, model_version):
    mo.md(f"""
    ## 1. Discover the meaning
    **{flights_model["name"]}** · semantic model `{".".join([*NAMESPACE, MODEL_NAME])}`,
    entity version {model_version}

    {flights_model["description"]}

    The model maps each dataset to an Iceberg table, names its key, states the joins
    between datasets and carries the agreed metrics. Its AI context:

    {"  \n".join(flights_model["ai_context"]["instructions"].splitlines())}
    """)


@app.cell
def _(flights_model, mo, plain_table):
    mo.vstack(
        [
            plain_table(
                [
                    {"dataset": _d["name"], "Iceberg table": _d["source"],
                     "primary key": ", ".join(_d["primary_key"]), "fields": len(_d["fields"])}
                    for _d in flights_model["datasets"]
                ],
                label="Datasets",
            ),
            plain_table(
                [
                    {"relationship": _r["name"], "join": f"{_r['from']}.{', '.join(_r['from_columns'])} → "
                     f"{_r['to']}.{', '.join(_r['to_columns'])}"}
                    for _r in flights_model["relationships"]
                ],
                label="Relationships (many to one)",
            ),
            plain_table(
                [
                    {"metric": _m["name"], "SQL": _m["expression"]["dialects"][0]["expression"],
                     "definition": _m["description"]}
                    for _m in flights_model["metrics"]
                ],
                label="Metrics",
            ),
        ]
    )


@app.cell
def _(NAMESPACE, bind_datasets, connect_duckdb, flights_model):
    # Each dataset becomes a view named like the dataset, over the table the model points at.
    lakehouse = connect_duckdb(NAMESPACE, "flights")
    bind_datasets(lakehouse, flights_model)
    return (lakehouse,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Verify the meaning against the data
    A model is only useful if the data honors it. These checks come from the model itself:
    every primary key must be unique, and every relationship must find its target row.
    The notebook stops if one fails.
    """)


@app.cell
def _(flights_model, lakehouse, model_checks, plain_table):
    checks = model_checks(lakehouse, flights_model)
    _failed = [_c["check"] for _c in checks if _c["violations"]]
    if _failed:
        raise ValueError("The data does not match its semantic model: " + "; ".join(_failed))
    plain_table(checks)
    return (checks,)


@app.cell
def _(checks, lakehouse, mo, plain_table):
    assert all(_check["violations"] == 0 for _check in checks)
    overview = mo.sql(
        """
        SELECT count(*) AS scheduled_flights,
               min(date) AS first_date_utc, max(date) AS last_date_utc,
               count(*) FILTER (WHERE cancelled) AS canceled_flights,
               count(*) FILTER (WHERE diverted) AS diverted_flights,
               count(*) FILTER (WHERE NOT cancelled AND NOT diverted) AS punctuality_denominator,
               count(DISTINCT route_id) AS routes, count(DISTINCT carrier_code) AS carriers
        FROM FLIGHT
    """,
        engine=lakehouse,
        output=False,
    )
    plain_table(overview)
    return (overview,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 3. “Which departure airports have the highest average departure delay?”
    The model answers two things. The join: follow `flight_route` and then
    `route_departure_airport`; `route_destination_airport` would answer a different
    question. The metric: `average_departure_delay` in minutes, where `AVG` ignores
    the missing delays of canceled flights. Both relationships are many to one, so every
    flight stays one row. The SQL below is assembled from the stored model.
    """)


@app.cell
def _(flights_model, joins, metric_sql, mo):
    airport_delay_sql = f"""
    SELECT AIRPORT.code AS departure_airport, AIRPORT.name,
           count(*) AS scheduled_flights, count(FLIGHT.dep_delay) AS observed_departures,
           round({metric_sql(flights_model, "average_departure_delay")}, 2) AS average_delay_minutes
    FROM FLIGHT
    {joins(flights_model, "flight_route", "route_departure_airport")}
    GROUP BY AIRPORT.code, AIRPORT.name
    ORDER BY average_delay_minutes DESC, departure_airport
    """
    mo.md(f"```sql{airport_delay_sql}```")
    return (airport_delay_sql,)


@app.cell
def _(airport_delay_sql, lakehouse, mo, plain_table):
    airport_delays = mo.sql(airport_delay_sql, engine=lakehouse, output=False)
    plain_table(airport_delays)
    return (airport_delays,)


@app.cell
def _(airport_delays, mo, plt):
    _figure, _axes = plt.subplots(figsize=(9, 4), layout="constrained")
    _axes.barh(airport_delays["departure_airport"], airport_delays["average_delay_minutes"])
    _axes.invert_yaxis()
    _axes.set(xlabel="Average departure delay (minutes)", ylabel="Synthetic departure airport")
    _chart = mo.as_html(_figure)
    plt.close(_figure)
    mo.vstack([_chart])


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 4. “Which carrier is most punctual?” needs a policy
    The stored `on_time_arrival_pct` is a product policy: **arrival delay < 15 minutes**
    among **noncanceled, nondiverted** flights. `cancellation_pct` uses **all scheduled
    flights** instead. Both expressions come from Polaris, so the definition you read
    and the calculation you run are the same text.
    """)


@app.cell
def _(flights_model, joins, lakehouse, metric_sql, mo, plain_table):
    carrier_performance = mo.sql(
        f"""
        SELECT CARRIER.name AS carrier, count(*) AS scheduled_flights,
               count(*) FILTER (WHERE NOT FLIGHT.cancelled AND NOT FLIGHT.diverted) AS eligible_arrivals,
               round({metric_sql(flights_model, "on_time_arrival_pct")}, 2) AS on_time_arrival_pct,
               round({metric_sql(flights_model, "cancellation_pct")}, 2) AS cancellation_pct
        FROM FLIGHT
        {joins(flights_model, "flight_carrier")}
        GROUP BY CARRIER.name ORDER BY on_time_arrival_pct DESC, carrier
    """,
        engine=lakehouse,
        output=False,
    )
    plain_table(carrier_performance)
    return (carrier_performance,)


@app.cell
def _(lakehouse, mo, plain_table):
    denominator_comparison = mo.sql(
        """
        SELECT count(*) AS all_scheduled,
               count(*) FILTER (WHERE NOT cancelled AND NOT diverted) AS eligible_arrivals,
               round(100.0 * count(*) FILTER (WHERE NOT cancelled AND NOT diverted AND arr_delay < 15)
                   / count(*), 2) AS on_time_share_of_all_scheduled_pct,
               round(100.0 * count(*) FILTER (WHERE NOT cancelled AND NOT diverted AND arr_delay < 15)
                   / nullif(count(*) FILTER (WHERE NOT cancelled AND NOT diverted), 0), 2)
                   AS on_time_share_of_eligible_arrivals_pct,
               round(avg(coalesce(dep_delay, 0)), 2) AS misleading_zero_filled_delay_minutes,
               round(avg(dep_delay), 2) AS observed_departure_delay_minutes
        FROM FLIGHT
    """,
        engine=lakehouse,
        output=False,
    )
    plain_table(denominator_comparison)
    return (denominator_comparison,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 5. A valid join can still produce the wrong metric
    The model's `runway_airport` relationship points from RUNWAY to AIRPORT: many runways
    per airport. Joining runways into a flight aggregate goes against that direction and
    multiplies each flight. Column names match either way; only the relationship tells
    them apart. The first count follows the model, the second shows the fan-out to avoid.
    """)


@app.cell
def _(flights_model, joins, lakehouse, mo, plain_table):
    join_cardinality = mo.sql(
        f"""
        SELECT (SELECT count(*) FROM FLIGHT) AS flight_instances,
               (SELECT count(*) FROM FLIGHT
                   {joins(flights_model, "flight_route", "route_departure_airport")}) AS correct_join_rows,
               (SELECT count(*) FROM FLIGHT
                   {joins(flights_model, "flight_route")}
                   JOIN RUNWAY ON ROUTE.orig_airport_code = RUNWAY.airport_code) AS runway_join_rows
    """,
        engine=lakehouse,
        output=False,
    )
    plain_table(join_cardinality)
    return (join_cardinality,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 6. The same meaning for an AI agent
    An agent with the platform's MCP server calls `describe_semantic_model` and gets this
    model: datasets, relationships, metrics and AI context, with the user's own permissions.
    Ask it to show the definitions and SQL it picked before interpreting results.

    **Example prompt:** “Use the flights semantic model in ai_flights to compare carriers'
    on-time arrival percentages for January 2026. State the grain, UTC time window,
    denominator and exclusions; show the SQL and label the result synthetic.”

    Reconnect after another publication to see fresh snapshots. A complete product would
    also define operational ownership, freshness targets and versioned releases.
    """)


if __name__ == "__main__":
    app.run()
