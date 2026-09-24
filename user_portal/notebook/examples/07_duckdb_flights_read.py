import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="07 · Read an AI-ready flights product", sql_output="pandas")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt

    from user_portal.notebook.display import plain_table
    from user_portal.notebook.duckdb_connection import connect_duckdb
    from user_portal.notebook.flights import bind_flights, load_semantics, quality_report, require_quality

    NAMESPACE = ["ai_flights"]
    return (
        NAMESPACE,
        plt,
        bind_flights,
        connect_duckdb,
        load_semantics,
        mo,
        quality_report,
        require_quality,
        plain_table,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # From flight records to an AI-ready data product
    **Example 7 of 7 · Native DuckDB SQL on Iceberg**

    Run **06 · Write an AI-ready flights product** first in the same database.
    This notebook uses a read-only catalog attachment and your own permissions.
    Queries scan the Iceberg tables directly; only their results become dataframes
    for display. No AI service, API key or external data download is needed.

    The story: **discover the meaning → verify the evidence → ask a precise
    question → show the SQL and its limits**. Every result below is synthetic.
    """)


@app.cell
def _(load_semantics, mo):
    model, product, fingerprints = load_semantics(mo.notebook_dir())
    mo.md(f"""
    ## 1. Discover the product
    **{product["name"]} · version {product["version"]}**

    {product["grain"]}

    **Owner:** {product["owner"]}  
    **Refresh:** {product["refresh"]}  
    **Time:** {product["time_convention"]}

    `flights.yaml` is the unchanged [Apache Ossie model](https://github.com/apache/ossie).
    `flights.product.yaml` adds the demo's metric policies, SQL checks and AI guidance.
    This example translates selected definitions into explicit SQL; it does not run
    an Ossie compiler or generate SQL with an LLM.
    """)
    return fingerprints, model, product


@app.cell
def _(mo, model, plain_table):
    semantic_terms = [
        {
            "concept": _concept["concept"],
            "relationship": _rel["name"],
            "meaning": " / ".join(_rel.get("verbalizes", [])),
            "derived definition": " / ".join(_rel.get("derived_by", [])),
        }
        for _concept in model["ontology"]
        if _concept["concept"] in {"Airport", "Route", "Flight"}
        for _rel in _concept.get("relationships", [])
        if _rel["name"] in {"departure", "destination", "route", "departure_delay", "average_departure_delay"}
    ]
    plain_table(semantic_terms)
    return (semantic_terms,)


@app.cell
def _(NAMESPACE, bind_flights, connect_duckdb, fingerprints, model):
    lakehouse = connect_duckdb(NAMESPACE, "flights")
    bind_flights(lakehouse, model, NAMESPACE, fingerprints)
    return (lakehouse,)


@app.cell
def _(lakehouse, mo, product, quality_report, require_quality, plain_table):
    checks = quality_report(lakehouse, product)
    require_quality(checks)
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
    ## 2. “Which departure airports have the highest average departure delay?”
    Ossie defines `Airport.average_departure_delay` through
    `Flight.route.departure`. Follow `FLIGHT.route_id → ROUTE.id →
    ROUTE.orig_airport_code → AIRPORT.code`. Using the destination column would
    answer a different question. Unique dimension keys keep one joined row per flight.

    `Delay` extends `NrMinutes`: report **minutes**, preserve negative (early)
    departures and let `AVG` ignore missing delays on canceled flights. This uses
    the full published date range. Display the number of observations with the mean.
    """)


@app.cell
def _(lakehouse, mo, product, plain_table):
    airport_delays = mo.sql(
        f"""
        SELECT a.code AS departure_airport, a.name,
               count(*) AS scheduled_flights, count(f.dep_delay) AS observed_departures,
               round({product["metrics"]["average_departure_delay"]["sql"]}, 2) AS average_delay_minutes
        FROM FLIGHT f
        JOIN ROUTE r ON f.route_id = r.id
        JOIN AIRPORT a ON r.orig_airport_code = a.code
        GROUP BY a.code, a.name
        ORDER BY average_delay_minutes DESC, departure_airport
    """,
        engine=lakehouse,
        output=False,
    )
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
    ## 3. “Which carrier is most punctual?” needs a policy
    Our demo defines on-time arrival as **arrival delay < 15 minutes**, among
    **noncanceled, nondiverted** flights. This is an additional product policy, not
    an Ossie definition. Cancellation rate uses **all scheduled flights** instead.
    The SQL expressions are read from `flights.product.yaml` so the displayed
    definition and executed calculation travel together.
    """)


@app.cell
def _(lakehouse, mo, product, plain_table):
    carrier_performance = mo.sql(
        f"""
        SELECT c.name AS carrier, count(*) AS scheduled_flights,
               count(*) FILTER (WHERE NOT f.cancelled AND NOT f.diverted) AS eligible_arrivals,
               round({product["metrics"]["on_time_arrival_pct"]["sql"]}, 2) AS on_time_arrival_pct,
               round({product["metrics"]["cancellation_pct"]["sql"]}, 2) AS cancellation_pct
        FROM FLIGHT f JOIN CARRIER c ON f.carrier_code = c.code
        GROUP BY c.name ORDER BY on_time_arrival_pct DESC, carrier
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
    ## 4. A valid join can still produce the wrong metric
    An airport has multiple runways. Joining them into a flight aggregation
    multiplies each flight. An AI consumer needs the relationship cardinality as
    well as matching column names. The first join below preserves the flight grain;
    the runway join demonstrates the fan-out to avoid.
    """)


@app.cell
def _(lakehouse, mo, plain_table):
    join_cardinality = mo.sql(
        """
        SELECT (SELECT count(*) FROM FLIGHT) AS flight_instances,
               (SELECT count(*) FROM FLIGHT f JOIN ROUTE r ON f.route_id = r.id
                   JOIN AIRPORT a ON r.orig_airport_code = a.code) AS correct_join_rows,
               (SELECT count(*) FROM FLIGHT f JOIN ROUTE r ON f.route_id = r.id
                   JOIN RUNWAY w ON r.orig_airport_code = w.airport_code) AS runway_join_rows
    """,
        engine=lakehouse,
        output=False,
    )
    plain_table(join_cardinality)
    return (join_cardinality,)


@app.cell(hide_code=True)
def _(mo, product):
    _guidance = "\n".join("- " + _item for _item in product["ai_guidance"])
    mo.md(f"""
    ## 5. Context to give an AI consumer
    Provide the ontology and product contract alongside the permitted table names.
    Ask the consumer to show its selected definitions and SQL before interpreting results.

    {_guidance}

    **Example prompt:** “Using the supplied flights ontology and product contract,
    compare carriers' on-time arrival percentages for January 2026. State the grain,
    UTC time window, denominator and exclusions; show SQL and label the result synthetic.”

    The hashes checked at connection time tie these local YAML files to the table
    metadata. Reconnect after another publication to see fresh snapshots. A complete
    product would also publish its semantic files in a discoverable registry and
    define operational ownership, freshness targets and versioned releases.
    """)


if __name__ == "__main__":
    app.run()
