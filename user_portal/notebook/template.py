import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    from user_portal.notebook.connection import connect
    from user_portal.notebook.display import plain_table

    catalog = connect()
    database = os.environ.get("ICEBERG_DATABASE_NAME", os.environ["ICEBERG_DATABASE"])
    namespace = tuple(json.loads(os.environ.get("ICEBERG_NAMESPACE", "[]")))
    shared_objects = json.loads(os.environ["ICEBERG_SHARED_OBJECTS"]) if "ICEBERG_SHARED_OBJECTS" in os.environ else None
    selected_table = os.environ.get("ICEBERG_TABLE", "")
    mo.md(f"# {database}\nShared team files for this environment. Operations use your own data permissions.")
    return (catalog, mo, namespace, selected_table, plain_table, shared_objects)


@app.cell
def _(catalog, mo, namespace, shared_objects):
    if shared_objects is None:
        namespaces = catalog.list_namespaces(namespace)
        tables = catalog.list_tables(namespace) if namespace else []
        views = catalog.list_views(namespace) if namespace else []
    else:
        namespaces = sorted({tuple(o["namespace"]) for o in shared_objects})
        tables = [(*o["namespace"], o["name"]) for o in shared_objects if o["kind"] == "table"]
        views = [(*o["namespace"], o["name"]) for o in shared_objects if o["kind"] == "view"]
    mo.vstack(
        [
            mo.md("## Available objects"),
            mo.md(f"**Namespaces:** {namespaces}\n\n**Tables:** {tables}\n\n**Views:** {views}"),
        ]
    )


@app.cell
def _(catalog, mo, namespace, selected_table, plain_table):
    mo.stop(
        not selected_table,
        mo.md(
            'Open a table from the portal or use `catalog.load_table(("namespace", "table"))` in a new cell.'
        ),
    )
    from pyiceberg.exceptions import ResolveError as _ResolveError

    try:
        table = catalog.load_table((*namespace, selected_table))
        df = table.scan(limit=100).to_pandas()
    except (ValueError, _ResolveError) as _error:
        # PyIceberg cannot read every Iceberg v3 type yet: variant fails to load, geometry to scan.
        mo.stop(
            True,
            mo.callout(
                f"PyIceberg cannot read this table ({_error}). "
                "Example 05 reads Iceberg v3 tables with DuckDB.",
                kind="warn",
            ),
        )
    plain_table(df)
    return df, table


@app.cell
def _(mo):
    mo.md("""
    ## Getting started
    Add Python or SQL cells in the editor. You can use `catalog`, `table` and `df`.
    Your work is saved as a Python notebook in shared storage for this team and environment.
    Run the cells with the ▶ button in marimo. Writing requires a user role with write permissions.
    """)


if __name__ == "__main__":
    app.run()
