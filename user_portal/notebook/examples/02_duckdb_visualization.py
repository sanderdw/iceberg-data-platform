import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="02 · Visualize with DuckDB", sql_output="pandas")


@app.cell
def _():
    import os

    import marimo as mo
    import matplotlib.pyplot as plt
    from pyiceberg.exceptions import ForbiddenError, NoSuchTableError

    from user_portal.notebook.connection import connect

    return ForbiddenError, NoSuchTableError, connect, mo, os, plt


@app.cell(hide_code=True)
def _(mo, os):
    mo.md(f"""
    # Visualize with DuckDB
    **Example 2 of 3 · database `{os.environ.get("ICEBERG_DATABASE_NAME", os.environ["ICEBERG_DATABASE"])}`**

    PyIceberg reads `synthetic.neighborhood_electricity` with your own user permissions.
    Then **DuckDB** analyzes the data in marimo's SQL cells. The charts react to your street selection.
    No additional connection or secrets are needed.

    Run the cells with **▶**. Create the table with example 1 first, or use your team's existing
    table. This notebook does not write data to Iceberg.
    """)


@app.cell(hide_code=True)
def _(mo):
    reload_data = mo.ui.run_button(label="Reload Iceberg data")
    mo.vstack([reload_data])
    return (reload_data,)


@app.cell
def _(ForbiddenError, NoSuchTableError, connect, mo, reload_data):
    _reload = reload_data.value
    try:
        _catalog = connect()
        _table = _catalog.load_table(("synthetic", "neighborhood_electricity"))
        _columns = (
            "timestamp_utc",
            "home_id",
            "street",
            "consumption_kwh",
            "generation_kwh",
            "grid_draw_kwh",
            "grid_export_kwh",
        )
        mo.stop(
            not set(_columns).issubset(_table.schema().column_names),
            mo.callout(
                "This table has a different schema from the example data. Select the correct database or adapt the analysis.",
                kind="warn",
            ),
        )
        mo.stop(
            _table.scan().count() > 100_000,
            mo.callout(
                "This example reads up to 100,000 rows into memory. Add an Iceberg filter first for a larger table.",
                kind="warn",
            ),
        )
        energy_frame = _table.scan(selected_fields=_columns).to_pandas()
    except NoSuchTableError:
        mo.stop(
            True,
            mo.callout(
                "The example table does not exist yet. Open 01 · Neighborhood data with PyIceberg, create the table, then click Reload Iceberg data here.",
                kind="info",
            ),
        )
    except ForbiddenError:
        mo.stop(True, mo.callout("Your user does not have read permissions for this table.", kind="warn"))
    mo.stop(
        energy_frame.empty, mo.callout("The table is empty. Populate it with example 1 first.", kind="info")
    )
    # Explicit local wall time avoids a DuckDB ICU/network dependency in this isolated runtime.
    energy_frame = energy_frame.assign(
        local_timestamp=energy_frame["timestamp_utc"].dt.tz_convert("Europe/Amsterdam").dt.tz_localize(None)
    )
    return (energy_frame,)


@app.cell(hide_code=True)
def _(energy_frame, mo):
    street_choice = mo.ui.dropdown(
        options=["All streets", *sorted(energy_frame["street"].unique())],
        value="All streets",
        label="Street",
    )
    mo.vstack([mo.md("## Explore the neighborhood"), street_choice])
    return (street_choice,)


@app.cell
def _(energy_frame, street_choice):
    filtered_energy = energy_frame.loc[
        (street_choice.value == "All streets") | (energy_frame["street"] == street_choice.value)
    ]
    return (filtered_energy,)


@app.cell
def _(filtered_energy, mo):
    hourly_energy = mo.sql(
        """
        SELECT date_trunc('hour', local_timestamp) AS hour,
               sum(consumption_kwh) AS consumption_kwh,
               sum(generation_kwh) AS generation_kwh,
               sum(grid_draw_kwh) AS grid_draw_kwh,
               sum(grid_export_kwh) AS grid_export_kwh
        FROM filtered_energy
        GROUP BY hour
        ORDER BY hour
        """,
        output=False,
    )
    return (hourly_energy,)


@app.cell
def _(energy_frame, mo):
    street_totals = mo.sql(
        """
        SELECT street, count(DISTINCT home_id) AS homes,
               sum(consumption_kwh) AS consumption_kwh,
               sum(generation_kwh) AS generation_kwh,
               sum(grid_draw_kwh) AS grid_draw_kwh,
               sum(grid_export_kwh) AS grid_export_kwh
        FROM energy_frame
        GROUP BY street
        ORDER BY street
        """,
        output=False,
    )
    return (street_totals,)


@app.cell(hide_code=True)
def _(hourly_energy, mo):
    mo.hstack(
        [
            mo.stat(label="Consumption", value=f"{hourly_energy['consumption_kwh'].sum():,.1f} kWh"),
            mo.stat(label="Solar generation", value=f"{hourly_energy['generation_kwh'].sum():,.1f} kWh"),
            mo.stat(label="Grid draw", value=f"{hourly_energy['grid_draw_kwh'].sum():,.1f} kWh"),
        ]
    )


@app.cell(hide_code=True)
def _(hourly_energy, mo, plt, street_choice):
    with plt.style.context("dark_background"):
        _figure, _axes = plt.subplots(figsize=(11, 4), layout="constrained")
        _axes.plot(
            hourly_energy["hour"], hourly_energy["consumption_kwh"], color="#edfc73", label="Consumption"
        )
        _axes.plot(
            hourly_energy["hour"], hourly_energy["generation_kwh"], color="#64c8dd", label="Solar generation"
        )
        _axes.set(
            title=f"Consumption and solar generation · {street_choice.value}",
            xlabel="Time · Europe/Amsterdam",
            ylabel="kWh per hour",
        )
        _axes.legend()
        _axes.grid(alpha=0.15)
        _figure.autofmt_xdate()
        hourly_plot = mo.as_html(_figure)
        plt.close(_figure)
    mo.vstack([hourly_plot])
    return (hourly_plot,)


@app.cell(hide_code=True)
def _(mo, plt, street_totals):
    with plt.style.context("dark_background"):
        _figure, _axes = plt.subplots(figsize=(11, 4), layout="constrained")
        _positions = range(len(street_totals))
        _axes.barh(
            [p + 0.2 for p in _positions],
            street_totals["grid_draw_kwh"],
            height=0.4,
            color="#edfc73",
            label="Grid draw",
        )
        _axes.barh(
            [p - 0.2 for p in _positions],
            street_totals["grid_export_kwh"],
            height=0.4,
            color="#64c8dd",
            label="Grid export",
        )
        _axes.set_yticks(list(_positions), street_totals["street"])
        _axes.set(title="All streets · grid draw and export", xlabel="kWh over the full period")
        _axes.legend()
        street_plot = mo.as_html(_figure)
        plt.close(_figure)
    mo.vstack([street_plot, mo.ui.table(street_totals.round(2), selection=None, label="Totals by street")])


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Explore further
    Edit the SQL cells: group by day, compare homes or calculate self-consumption.
    The street filter recalculates the hourly query and its chart without reading from Iceberg again.
    **Reload Iceberg data** fetches a fresh snapshot when another notebook has changed the table.

    SQL runs locally on the loaded data; no DuckDB extensions or internet downloads are needed.
    """)


if __name__ == "__main__":
    app.run()
