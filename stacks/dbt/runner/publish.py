"""Stamp the Bridge table-property conventions (contracts/bridge/v1/table-properties.md) after a build.

For every table this run materialized, it records the producer, the revision, the upstream tables
and the tests' outcome, and copies model and column descriptions into Iceberg (`comment` and each
field's `doc`). These are metadata-only catalog commits made with the run's own write token, so any
engine and the platform's catalog browser see where a table comes from.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PREFIX = "iceberg-data-platform."
MATERIALIZED = {"model", "seed", "snapshot"}
MAX_INPUTS = 50


def load(target):
    info = Path(target) / "info_schema" / "v1"
    connection = duckdb.connect()

    def table(name, columns):
        path = info / f"{name}.parquet"
        if not path.exists():
            return []
        return [dict(zip(columns, row, strict=True)) for row in connection.execute(
            f"SELECT {', '.join(columns)} FROM read_parquet(?)", [str(path)]).fetchall()]

    relation = ["unique_id", "name", "description", "database_name", "schema_name", "identifier", "alias"]
    nodes = {}
    for kind, name in (("model", "dbt.models"), ("seed", "dbt.seeds"), ("snapshot", "dbt.snapshots"),
                       ("source", "dbt.sources")):
        for row in table(name, relation):
            nodes[row["unique_id"]] = {**row, "kind": kind}
    columns = {}
    for row in table("dbt.node_columns", ["node_unique_id", "column_name", "description"]):
        if row["description"]:
            columns.setdefault(row["node_unique_id"], {})[row["column_name"]] = row["description"]
    parents = {}
    for row in table("dbt.edges", ["parent_unique_id", "child_unique_id"]):
        parents.setdefault(row["child_unique_id"], []).append(row["parent_unique_id"])
    tests = {row["unique_id"]: row["node_unique_id"] for row in table("dbt.data_tests",
                                                                        ["unique_id", "node_unique_id"])}
    return nodes, columns, parents, tests


def quality(node, results, tests):
    outcome = {"pass": 0, "warn": 0, "fail": 0}
    for test, subject in tests.items():
        status = results.get(test)
        if subject != node or not status:
            continue
        outcome["fail" if status in ("fail", "error") else "warn" if status == "warn" else "pass"] += 1
    if not any(outcome.values()):
        return None
    status = "fail" if outcome["fail"] else "warn" if outcome["warn"] else "pass"
    summary = ", ".join(f"{count} {label}" for label, count in (("passed", outcome["pass"]),
                                                                ("warning", outcome["warn"]),
                                                                ("failed", outcome["fail"])) if count)
    return status, summary


def properties(job, node, nodes, parents, results, tests, now):
    catalogs = job["catalogs"]
    inputs = []
    for parent in parents.get(node["unique_id"], []):
        upstream = nodes.get(parent)
        if upstream and upstream["database_name"] in catalogs:
            inputs.append({"database": catalogs[upstream["database_name"]],
                           "namespace": upstream["schema_name"].split("."),
                           "name": upstream["identifier"] or upstream["alias"] or upstream["name"]})
    values = {
        PREFIX + "producer": "dbt",
        PREFIX + "producer.version": job.get("version", "dbt"),
        PREFIX + "producer.url": f"{job['origin']}/#/projects/{job['project']}/pipeline?environment="
                                 f"{job['environment']}&node={node['unique_id']}",
        PREFIX + "producer.run-id": job["run"],
        PREFIX + "producer.run-at": now,
        PREFIX + "producer.source-revision": job["revision"],
        PREFIX + "producer.node": node["unique_id"],
        PREFIX + "lineage.inputs": json.dumps(inputs[:MAX_INPUTS], separators=(",", ":")),
        PREFIX + "lineage.inputs-truncated": str(len(inputs) > MAX_INPUTS).lower(),
    }
    checked = quality(node["unique_id"], results, tests)
    if checked:
        values[PREFIX + "quality.status"], values[PREFIX + "quality.summary"] = checked[0], checked[1][:200]
        values[PREFIX + "quality.checked-at"] = now
    if node.get("description"):
        values["comment"] = node["description"][:2000]
    return values


def main(job, target, token):
    from pyiceberg.catalog import load_catalog

    run_results = json.loads((Path(target) / "run_results.json").read_text())["results"]
    results = {r["unique_id"]: r["status"] for r in run_results}
    nodes, columns, parents, tests = load(target)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    published, errors, catalogs = [], [], {}
    for node in nodes.values():
        if node["kind"] not in MATERIALIZED or results.get(node["unique_id"]) != "success":
            continue
        warehouse = job["catalogs"].get(node["database_name"])
        if not warehouse:
            continue
        identifier = (*node["schema_name"].split("."), node["identifier"] or node["alias"] or node["name"])
        try:
            if warehouse not in catalogs:
                catalogs[warehouse] = load_catalog(f"platform-{warehouse}", type="rest", uri=job["catalogUri"],
                                                   warehouse=warehouse, token=token)
            table = catalogs[warehouse].load_table(identifier)
            docs = columns.get(node["unique_id"], {})
            with table.transaction() as transaction:
                transaction.set_properties(properties(job, node, nodes, parents, results, tests, now))
                fields = {f.name: f for f in table.schema().fields}
                changed = {name: text[:1000] for name, text in docs.items()
                           if name in fields and (fields[name].doc or "") != text[:1000]}
                if changed:
                    with transaction.update_schema() as schema:
                        for name, text in changed.items():
                            schema.update_column(name, doc=text)
            published.append(".".join(identifier))
        except Exception as exc:  # noqa: BLE001 - One table's metadata never blocks the others.
            errors.append({"table": ".".join(identifier), "error": type(exc).__name__})
    return {"tables": published, "errors": errors}
