"""How a result is best shown, decided from its columns and rows rather than by the LLM.

The agent passes the recommendation on unchanged, unless the person asks for another view:

- one metric without dimensions: a KPI;
- a time dimension: a line, split into series by a second dimension;
- one category dimension: bars, horizontal when there are many or long labels;
- two category dimensions and one metric: bars split into series, stacked only when the metric
  adds up across groups (a SUM or COUNT; averages and ratios never stack);
- anything wider, a single row with dimensions, or too many bars: a table.

A dimension with a single value in the result is left out first, so one month by carrier is bars.
"""

from .semantic.compiler import TIME_TYPES

MAX_BARS = 40
MAX_LINES = 4
LONG_LABEL = 12
MANY_BARS = 8


def recommend(columns, rows):
    dims = [(i, c) for i, c in enumerate(columns) if c["kind"] == "dimension"]
    metrics = [c for c in columns if c["kind"] == "metric"]
    if not rows or not metrics:
        return {"tool": "show_table", "arguments": {}}
    if not dims:
        if len(metrics) == 1:
            return {"tool": "show_kpi", "arguments": {"column": metrics[0]["name"]}}
        return {"tool": "show_table", "arguments": {}}
    if len(rows) > 1:
        # A dimension with one value (a single month, say) splits nothing: chart by the others.
        varying = [(i, c) for i, c in dims if len({str(row[i]) for row in rows}) > 1]
        dims = varying or dims
    if len(rows) == 1 or len(dims) > 2 or (len(dims) == 2 and len(metrics) > 1):
        return {"tool": "show_table", "arguments": {}}
    time = [(i, c) for i, c in dims if c.get("grain") or c.get("type") in TIME_TYPES]
    x_index, x = time[0] if time else dims[0]
    other = [c for i, c in dims if i != x_index]
    chart = {"kind": "line" if time else "bar", "x": x["name"], "y": [m["name"] for m in metrics[:MAX_LINES]]}
    if other:
        chart |= {"y": [metrics[0]["name"]], "series": other[0]["name"]}
    if not time:
        labels = {str(row[x_index]) for row in rows}
        if len(labels) > MAX_BARS:
            return {"tool": "show_table", "arguments": {}}
        if len(labels) > MANY_BARS or max(map(len, labels)) > LONG_LABEL:
            chart["horizontal"] = True
        if other and metrics[0].get("additive"):
            chart["stacked"] = True
    return {"tool": "show_chart", "arguments": chart}
