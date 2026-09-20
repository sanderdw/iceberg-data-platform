"""Read a trusted gateway request on stdin; return bounded data and a dbt Charts SVG.

Invoked inside a disposable restricted container. No gateway/platform secrets or
user notebook mounts are available. Provider exceptions are never printed.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from .query import ReportQuery, read_report

MAX_OUTPUT = 4_000_000


def chart_number(value):
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Value cannot be charted")
    return result


def board_definition(data):
    """An ephemeral rendering document containing only aggregate query results."""
    rows = [[period, category, chart_number(total)] for period, category, total in data["rows"]]
    board = {
        "title": "Live Iceberg report",
        "queries": {
            "summary": {
                "type": "values",
                "columns": ["records", "total"],
                "values": [[chart_number(data["records"]), chart_number(data["total"])]],
            },
        },
        "charts": {
            "records": {"type": "kpi", "label": "Records", "query": "summary", "value": "records"},
            "total": {"type": "kpi", "label": "Total", "query": "summary", "value": "total"},
        },
        "rows": [{"cols": ["records", "total"]}],
    }
    if rows:
        board["queries"]["trend"] = {
            "type": "values",
            "columns": ["period", "category", "total"],
            "values": rows,
        }
        board["charts"]["trend"] = {
            "type": "line",
            "title": "Total by period and category (UTC)",
            "query": "trend",
            "x": "period",
            "y": "total",
            "color": "category",
        }
        board["rows"].append("trend")
    return board


def render_board(data):
    board = board_definition(data)
    return render_document(board), board


def render_document(board):
    with tempfile.TemporaryDirectory(prefix="iceberg-board-") as directory:
        root = Path(directory)
        (root / "dbt_charts.yml").write_text("{}\n")
        (root / "board.yml").write_text(yaml.safe_dump(board, sort_keys=False))
        # The renderer gets no catalog credentials or inherited environment.
        # It only sees the small values board; there is no warehouse connection.
        completed = subprocess.run(
            [
                str(Path(sys.executable).with_name("dct")),
                "render",
                str(root / "board.yml"),
                "--project-dir",
                directory,
                "--format",
                "svg",
                "--output",
                str(root / "board.svg"),
                "--no-cache",
            ],
            env={
                "PATH": os.defpath,
                "HOME": directory,
                "DO_NOT_TRACK": "1",
                "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            },
            cwd=directory,
            capture_output=True,
            timeout=20,
            check=False,
        )
        svg_path = root / "board.svg"
        if completed.returncode or not svg_path.exists() or svg_path.stat().st_size > MAX_OUTPUT:
            raise ValueError("Chart rendering failed")
        return svg_path.read_text()


def execute(request):
    if "definition" in request:
        from .execution import execute_definition

        return execute_definition(request)
    started = time.monotonic()
    data = read_report(request["source"], ReportQuery.model_validate(request["query"]))
    queried = time.monotonic()
    svg, board = render_board(data)
    return {
        "data": data,
        "svg": svg,
        "board": board,
        "timing": {
            "querySeconds": round(queried - started, 3),
            "renderSeconds": round(time.monotonic() - queried, 3),
        },
    }


def main():
    try:
        # The outer container enforces wall time, memory, processes and disk.
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        request = os.environ.pop("REPORT_REQUEST", None) or sys.stdin.buffer.read(65_537)
        if len(request) > 65_536:
            raise ValueError("Request is too large")
        result = json.dumps(execute(json.loads(request)), allow_nan=False)
        if len(result.encode()) > MAX_OUTPUT:
            raise ValueError("Result is too large")
        print(result)
    except Exception:  # noqa: BLE001 - Provider exceptions can contain credentials and SQL.
        print(
            '{"error":"Report unavailable. Check the selected columns, permissions and snapshot; then refresh."}'
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
