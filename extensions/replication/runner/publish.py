"""After a sync: what each table received in this run, and the Bridge table-property conventions.

Both come from the Iceberg REST catalog (contracts/bridge/v1/table-properties.md in the core): the
snapshots this run committed give the counts, and one metadata-only commit per table records the
producer, run and change log position with the run's write token. Standard library only.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

PREFIX = "iceberg-data-platform."


class Catalog:
    def __init__(self, uri, warehouse, token):
        self.uri, self.token = uri.rstrip("/"), token
        config = self.call("GET", f"/v1/config?warehouse={urllib.parse.quote(warehouse)}")
        prefix = (config.get("overrides") or {}).get("prefix") or (config.get("defaults") or {}).get("prefix") or ""
        self.base = f"/v1/{urllib.parse.quote(prefix)}" if prefix else "/v1"

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.uri + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        return json.loads(data) if data else {}

    def table_path(self, namespace, table):
        return f"{self.base}/namespaces/{urllib.parse.quote(namespace)}/tables/{urllib.parse.quote(table)}"

    def load(self, namespace, table):
        return self.call("GET", self.table_path(namespace, table))["metadata"]

    def set_properties(self, namespace, table, values):
        self.call("POST", self.table_path(namespace, table),
                  {"requirements": [], "updates": [{"action": "set-properties", "updates": values}]})


def counts(metadata, since_ms):
    """Rows and delete files the snapshots since `since_ms` added."""
    totals = {"records": 0, "replaced": 0, "dataFiles": 0, "deleteFiles": 0, "snapshots": 0}
    for snapshot in metadata.get("snapshots") or []:
        if snapshot.get("timestamp-ms", 0) < since_ms:
            continue
        summary = snapshot.get("summary") or {}
        totals["snapshots"] += 1
        totals["records"] += int(summary.get("added-records", 0))
        # Rows an update or delete replaced: positional deletes (v2) or deletion vectors (v3).
        totals["replaced"] += int(summary.get("added-position-deletes", 0))
        totals["dataFiles"] += int(summary.get("added-data-files", 0))
        totals["deleteFiles"] += int(summary.get("added-delete-files", 0)) + int(summary.get("added-dvs", 0))
    return totals


def properties(job, name, totals, position, version, now):
    summary = (f"{totals['records']:,} rows written, {totals['replaced']:,} replaced or deleted"
               if totals["snapshots"] else "No changes in this run")
    values = {
        PREFIX + "producer": "olake",
        PREFIX + "producer.version": f"OLake {version}".strip(),
        PREFIX + "producer.url": job["url"],
        PREFIX + "producer.run-id": job["runId"],
        PREFIX + "producer.run-at": now,
        PREFIX + "producer.node": f"{job['sourceType']}.{name}",
        PREFIX + "quality.status": "pass",
        PREFIX + "quality.summary": summary[:200],
        PREFIX + "quality.checked-at": now,
    }
    if position:
        values[PREFIX + "producer.source-revision"] = f"LSN {position}"
    return values


def main(job, token, since_ms, position, version):
    now = datetime.now(UTC).isoformat(timespec="seconds")
    tables, published, errors = [], [], []
    try:
        catalog = Catalog(job["catalogUri"], job["warehouse"], token)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return [], {"tables": [], "errors": [{"table": "*", "error": type(exc).__name__}]}
    for name, spec in sorted(job["tables"].items()):
        identifier = f"{job['namespace']}.{spec['table']}"
        try:
            totals = counts(catalog.load(job["namespace"], spec["table"]), since_ms)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # nothing written yet, such as an empty first copy
                tables.append({"name": name, "table": spec["table"], "records": 0, "replaced": 0, "deleteFiles": 0})
                continue
            errors.append({"table": identifier, "error": f"HTTP {exc.code}"})
            continue
        except (OSError, urllib.error.URLError, ValueError, KeyError) as exc:
            errors.append({"table": identifier, "error": type(exc).__name__})
            continue
        tables.append({"name": name, "table": spec["table"], "records": totals["records"],
                       "replaced": totals["replaced"], "deleteFiles": totals["deleteFiles"]})
        try:
            catalog.set_properties(job["namespace"], spec["table"],
                                   properties(job, name, totals, position, version, now))
            published.append(identifier)
        except (OSError, urllib.error.URLError, ValueError) as exc:  # One table never blocks the others.
            errors.append({"table": identifier, "error": getattr(exc, "code", None) or type(exc).__name__})
    return tables, {"tables": published, "errors": errors}
