import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="01 · Neighborhood data with PyIceberg")


@app.cell
def _():
    import os

    import marimo as mo
    from pyiceberg.exceptions import ForbiddenError

    from user_portal.notebook.connection import connect
    from user_portal.notebook.display import plain_table
    from user_portal.notebook.synthetic import generate_energy_data

    return (ForbiddenError, connect, generate_energy_data, mo, os, plain_table)


@app.cell(hide_code=True)
def _(mo, os):
    mo.md(f"""
    # Neighborhood data with PyIceberg
    **Example 1 of 7 · database `{os.environ.get("ICEBERG_DATABASE_NAME", os.environ["ICEBERG_DATABASE"])}`**

    We create `synthetic.neighborhood_electricity`: **40 homes × 7 days × 96 quarter-hours = 26,880 rows**.
    The data comes from the bundled synthetic energy model. All addresses are fictional;
    `0000 ZZ` is a sample postal code. This is a demonstration, not a calibrated energy model.

    Consumption and generation are **kWh per quarter-hour**; solar capacity is **kWp**. The profiles start on
    September 1, 2026 in **Europe/Amsterdam**; Iceberg stores timestamps in UTC.

    Run the notebook from top to bottom to preview, write and read the data.
    This requires write permissions. Your own database and team permissions still apply.
    """)


@app.cell
def _(generate_energy_data):
    energy_data = generate_energy_data(homes=40, days=7)
    energy_identifier = ("synthetic", "neighborhood_electricity")
    return energy_data, energy_identifier


@app.cell(hide_code=True)
def _(energy_data, mo, plain_table):
    mo.vstack(
        [
            mo.md("## 1. Preview the generated data"),
            plain_table(energy_data.slice(0, 12), label="Preview · first 12 quarter-hours"),
            mo.plain_text(str(energy_data.schema)),
        ]
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## 2. Write to Iceberg
    Running the next cell creates and populates the table. Populated tables are
    skipped, so rerunning the notebook does not duplicate or overwrite rows.
    """)


@app.cell
def _(ForbiddenError, connect, energy_data, energy_identifier, mo):
    try:
        _catalog = connect()
        _catalog.create_namespace_if_not_exists(energy_identifier[:-1])
        _table = _catalog.create_table_if_not_exists(
            energy_identifier,
            schema=energy_data.schema,
            properties={
                "description": "Synthetic quarter-hour data for 40 fictional homes; all addresses are invented."
            },
        )
        _existing_rows = _table.scan().count()
        if _existing_rows == 0:
            _table.append(energy_data)
            write_message = f"Created and populated: {energy_data.num_rows:,} rows."
        else:
            write_message = f"Table already contains {_existing_rows:,} rows; append skipped."
        stored_table = _catalog.load_table(energy_identifier)
    except ForbiddenError:
        mo.stop(
            True,
            mo.callout(
                "Your user does not have write permissions for this database. A writer can create the example table; readers can then use example 2.",
                kind="warn",
            ),
        )
    return stored_table, write_message


@app.cell(hide_code=True)
def _(mo, stored_table, write_message, plain_table):
    mo.vstack(
        [
            mo.callout(write_message, kind="success"),
            mo.md("## 3. Read back the stored data"),
            plain_table(stored_table.scan(limit=10).to_arrow(), label="Read test from Iceberg"),
            mo.md(
                "Open **02 · Visualize with DuckDB** using the notebook selector above. Refresh the catalog to see the new namespace and table in the portal."
            ),
        ]
    )


if __name__ == "__main__":
    app.run()
