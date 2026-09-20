"""Execute a validated portal report, then generate a dbt Charts values board."""

import math
import time

from user_portal.preview import connect, sql_identifier

from .definitions import Report, report_sql
from .query import exact_value, load_table


def scalar(value, kind):
    if value is None:
        return None
    if kind in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UBIGINT", "UINTEGER"}:
        return exact_value(int(value))
    if kind in {"FLOAT", "DOUBLE"}:
        return exact_value(float(value))
    if kind == "BOOLEAN":
        return value == "true"
    return exact_value(value)


def read_definition(request):
    report = Report.model_validate(request["definition"])
    sql, parameters = report_sql(report)
    parameters = dict(parameters)
    source = request["source"]
    loaded = load_table(source)
    metadata = loaded["metadata"]
    if source["tableUuid"] != metadata["table-uuid"] or source["schemaId"] != metadata["current-schema-id"]:
        raise ValueError("The table or schema changed. Refresh the report.")
    snapshot = source["snapshotId"]
    if snapshot is not None and snapshot not in {
        str(s["snapshot-id"]) for s in metadata.get("snapshots", [])
    }:
        raise ValueError("The snapshot expired. Refresh the report.")
    fields = next(s["fields"] for s in metadata["schemas"] if s["schema-id"] == source["schemaId"])
    columns = {f["name"] for f in fields}
    if report.mode == "builder":
        builder = report.builder
        referenced = (
            set(builder.columns)
            | {d.column for d in builder.dimensions}
            | {f.column for f in builder.filters}
        )
        if builder.aggregate not in ("rows", "count"):
            referenced.add(builder.measure)
        if not referenced <= columns:
            raise ValueError("A selected column no longer exists. Update the report.")
    relation = ".".join(
        sql_identifier(p) for p in ("lakehouse", ".".join(source["namespace"]), source["table"])
    )
    if snapshot is not None:
        if not str(snapshot).isdigit() or len(str(snapshot)) > 19:
            raise ValueError("Invalid snapshot.")
        relation += f" AT (VERSION => {int(snapshot)})"
    base = f"SELECT * FROM {relation}" + (" WHERE false" if snapshot is None else "")
    connection = connect(source, loaded)
    try:
        connection.execute("SET TimeZone = ?", [report.timezone])
        connection.execute(f"CREATE TEMP VIEW report_base AS {base}")
        source_query = "SELECT * FROM report_base"
        if request.get("filterColumn") and request.get("filterValue") is not None:
            if request["filterColumn"] not in columns:
                raise ValueError("A dashboard filter column no longer exists.")
            source_query += f" WHERE {sql_identifier(request['filterColumn'])} = $__dashboard_filter"
            parameters["__dashboard_filter"] = request["filterValue"]
        bounded = f"WITH source AS ({source_query}) SELECT * FROM ({sql}) AS report_result LIMIT 1001"
        described = connection.execute("DESCRIBE " + bounded, parameters).fetchall()
        names = [row[0] for row in described]
        if len(names) > 20 or len(set(names)) != len(names):
            raise ValueError("Select at most 20 uniquely named output columns.")
        # Convert inside DuckDB so nanosecond timestamps, decimals and large IDs
        # remain exact. Numeric chart values use a separate projection below.
        selection = ", ".join(f"CAST({sql_identifier(name)} AS VARCHAR)" for name in names)
        rows = connection.execute(
            f"SELECT {selection} FROM ({bounded}) AS typed_result", parameters
        ).fetchmany(1001)
        if len(rows) > 1000:
            raise ValueError("More than 1,000 rows. Aggregate, filter or add a LIMIT.")
        return {
            "columns": [{"name": name, "type": kind} for name, kind, *_ in described],
            "rows": [[scalar(value, described[i][1]) for i, value in enumerate(row)] for row in rows],
            "snapshotId": snapshot,
            "tableUuid": source["tableUuid"],
            "schemaId": source["schemaId"],
            "timezone": report.timezone,
        }, report
    finally:
        connection.close()


def values_board(data, report):
    viz = report.visualization
    names = [c["name"] for c in data["columns"]]
    if viz.kind == "table":
        return None  # Native accessible table retains exact values.
    fields = [viz.y] if viz.kind == "kpi" else [viz.x, viz.y] + ([viz.color] if viz.color else [])
    if any(field not in names for field in fields):
        raise ValueError("Choose chart axes from the query output columns.")
    if not data["rows"]:
        return None
    numeric = {viz.y} | ({viz.x} if viz.kind == "scatter" else set())
    rows = []
    for row in data["rows"]:
        projected = list(row)
        for name in numeric:
            index = names.index(name)
            if row[index] is not None:
                projected[index] = float(row[index])
                if not math.isfinite(projected[index]):
                    raise ValueError("Choose finite numeric chart measures.")
        rows.append(projected)
    if viz.kind == "kpi" and len(rows) != 1:
        raise ValueError("A KPI needs exactly one result row. Remove grouping or use a different chart.")
    chart = {"type": viz.kind, "query": "result"}
    if viz.kind == "kpi":
        # dbt Charts treats KPI labels as Jinja. The portal already renders the
        # report name as plain text; never send author text into that template.
        chart.update(value=viz.y, label="Value")
    else:
        chart.update(x=viz.x, y=viz.y)
        if viz.color:
            chart["color"] = viz.color
    return {
        "queries": {"result": {"type": "values", "columns": names, "values": rows}},
        "charts": {"result": chart},
        "rows": ["result"],
    }


def execute_definition(request):
    from .worker import render_document

    started = time.monotonic()
    data, report = read_definition(request)
    queried = time.monotonic()
    board = values_board(data, report)
    svg = render_document(board) if board else None
    return {
        "data": data,
        "svg": svg,
        "timing": {
            "querySeconds": round(queried - started, 3),
            "renderSeconds": round(time.monotonic() - queried, 3),
        },
    }
