"""The pipeline graph of a project in an environment, from dbt v2's Parquet information schema.

The structure (nodes, edges, descriptions, columns, tests) comes from the most recent run in the
environment that produced `target/info_schema/v1`. Each node's status is its latest result across
runs, so a partial run (`--select`) updates only what it touched.
"""

import threading
from collections import OrderedDict
from pathlib import Path

import duckdb

from .errors import DbtError

KINDS = {"dbt.models": "model", "dbt.seeds": "seed", "dbt.snapshots": "snapshot", "dbt.sources": "source",
         "dbt.exposures": "exposure"}
TEST_STATUS = {"pass": "pass", "success": "pass", "warn": "warn", "fail": "fail", "error": "fail"}


def read(info, name, columns):
    path = Path(info) / f"{name}.parquet"
    if not path.exists():
        return []
    connection = duckdb.connect()
    try:
        described = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        available = {row[0] for row in described}
        wanted = [c for c in columns if c in available]
        rows = connection.execute(f"SELECT {', '.join(wanted)} FROM read_parquet(?)", [str(path)]).fetchall()
        return [{**dict.fromkeys(columns), **dict(zip(wanted, row, strict=True))} for row in rows]
    finally:
        connection.close()


class GraphCache:
    """Parsed structure per run; the artifacts of a finished run never change."""

    def __init__(self, size=32):
        self.items = OrderedDict()
        self.size = size
        self.lock = threading.Lock()

    def get(self, run_id, info):
        with self.lock:
            if run_id in self.items:
                self.items.move_to_end(run_id)
                return self.items[run_id]
        structure = load(info)
        with self.lock:
            self.items[run_id] = structure
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return structure


def load(info):
    relation = ["unique_id", "name", "description", "database_name", "schema_name", "identifier", "alias",
                "materialized", "tags", "original_file_path", "raw_code", "compiled_code", "package_name"]
    nodes = {}
    for table, kind in KINDS.items():
        for row in read(info, table, relation):
            nodes[row["unique_id"]] = {
                "id": row["unique_id"], "name": row["name"], "type": kind,
                "database": row["database_name"], "namespace": row["schema_name"],
                "table": row["identifier"] or row["alias"] or row["name"],
                "materialized": row["materialized"], "description": row["description"] or "",
                "tags": list(row["tags"] or []), "path": row["original_file_path"],
                "sql": row["raw_code"] or "", "compiledSql": row["compiled_code"] or "",
                "package": row["package_name"],
            }
    columns = {}
    for row in read(info, "dbt.node_columns", ["node_unique_id", "column_name", "column_index", "data_type",
                                               "description"]):
        columns.setdefault(row["node_unique_id"], []).append(
            {"name": row["column_name"], "type": row["data_type"], "description": row["description"] or "",
             "index": row["column_index"]})
    tests = {}
    for row in read(info, "dbt.data_tests", ["unique_id", "name", "node_unique_id", "column_name", "test_name",
                                             "severity"]):
        tests[row["unique_id"]] = {"id": row["unique_id"], "name": row["name"], "node": row["node_unique_id"],
                                   "column": row["column_name"], "test": row["test_name"], "severity": row["severity"]}
    edges = [{"from": r["parent_unique_id"], "to": r["child_unique_id"]}
             for r in read(info, "dbt.edges", ["parent_unique_id", "child_unique_id"])
             if r["parent_unique_id"] in nodes and r["child_unique_id"] in nodes]
    project = (read(info, "dbt.project", ["project_name", "dbt_version"]) or [{}])[0]
    # Nodes of installed packages stay in the graph but are marked, and macros never are nodes.
    compiled = Path(info).parents[1] / "compiled"
    for id, node in nodes.items():
        node["columns"] = sorted(columns.get(id, []), key=lambda c: (c["index"] is None, c["index"] or 0))
        package = node.pop("package")
        node["own"] = package in (None, project.get("project_name"))
        # dbt v2 keeps compiled SQL in target/compiled rather than in the information schema.
        if not node["compiledSql"] and package and node["path"]:
            path = (compiled / package / node["path"]).resolve()
            if path.is_relative_to(compiled.resolve()) and path.is_file():
                node["compiledSql"] = path.read_text(errors="replace")[:200_000]
    return {"nodes": nodes, "edges": edges, "tests": tests, "project": project}


def graph(structure, latest, *, catalogs, detail=False):
    results = {r["node"]: r for r in latest}
    tests_by_node = {}
    for test in structure["tests"].values():
        status = TEST_STATUS.get((results.get(test["id"]) or {}).get("status"), "unknown")
        tests_by_node.setdefault(test["node"], []).append({**test, "status": status})
    nodes = []
    for id, node in structure["nodes"].items():
        result = results.get(id) or {}
        counts = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
        for test in tests_by_node.get(id, []):
            counts[test["status"]] += 1
        item = {k: v for k, v in node.items() if detail or k not in ("sql", "compiledSql", "columns")}
        item.update(
            databaseId=catalogs.get(node["database"]), status=result.get("status", "never_run"),
            lastRun=result.get("run"), lastRunAt=result.get("finished_at"),
            executionTime=result.get("execution_time"), rowsAffected=result.get("rows_affected"),
            message=result.get("message") if result.get("status") in ("error", "fail") else None,
            tests=counts,
        )
        if detail:
            item["testResults"] = tests_by_node.get(id, [])
        nodes.append(item)
    return {"nodes": sorted(nodes, key=lambda n: n["id"]), "edges": structure["edges"],
            "dbtVersion": structure["project"].get("dbt_version")}


def neighbours(structure, node, depth):
    if node not in structure["nodes"]:
        raise DbtError(404, "No such node in this pipeline.", "unknown_node")
    parents, children = {}, {}
    for edge in structure["edges"]:
        parents.setdefault(edge["to"], []).append(edge["from"])
        children.setdefault(edge["from"], []).append(edge["to"])

    def walk(start, links):
        seen, frontier = {}, [start]
        for level in range(1, depth + 1):
            frontier = [n for current in frontier for n in links.get(current, []) if n not in seen and n != start]
            for n in frontier:
                seen.setdefault(n, level)
        return [{"id": n, "distance": d, "type": structure["nodes"][n]["type"]} for n, d in sorted(seen.items())]

    return {"node": node, "upstream": walk(node, parents), "downstream": walk(node, children)}
