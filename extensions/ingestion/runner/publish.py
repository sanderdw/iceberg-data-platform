"""Stamp the Bridge table-property conventions (contracts/bridge/v1/table-properties.md) after a load.

dlt's own `iceberg_table_properties` apply only when a table is created, so every run commits the
producer, run and quality properties itself: a metadata-only commit with the run's write token.
"""

from datetime import UTC, datetime

PREFIX = "iceberg-data-platform."


def revision(state, resource):
    """The resource's incremental cursor after this run, when it has one."""
    for source in (state.get("sources") or {}).values():
        incremental = ((source or {}).get("resources") or {}).get(resource, {}).get("incremental") or {}
        for value in incremental.values():
            if isinstance(value, dict) and value.get("last_value") is not None:
                return str(value["last_value"])[:200]
    return None


def properties(job, table, rows, load_id, version, cursor, now):
    values = {
        PREFIX + "producer": "dlt",
        PREFIX + "producer.version": f"dlt {version}",
        PREFIX + "producer.url": job["url"],
        PREFIX + "producer.run-id": load_id,
        PREFIX + "producer.run-at": now,
        PREFIX + "producer.node": f"{job['sourceType']}.{table}",
        PREFIX + "quality.status": "pass" if rows else "warn",
        PREFIX + "quality.summary": f"{rows:,} rows loaded by dlt" if rows else "No new rows in this run",
        PREFIX + "quality.checked-at": now,
    }
    if cursor:
        values[PREFIX + "producer.source-revision"] = cursor
    return values


def main(job, catalog, tables, load_id, version, state, roots):
    """`roots` maps each loaded table (child tables like `customers__tags` included) to its resource."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    published, errors = [], []
    for name, rows in tables.items():
        identifier = (job["namespace"], name)
        try:
            table = catalog.load_table(identifier)
            cursor = revision(state, roots.get(name, name))
            with table.transaction() as transaction:
                transaction.set_properties(properties(job, name, rows, load_id, version, cursor, now))
            published.append(".".join(identifier))
        except Exception as exc:  # noqa: BLE001 - One table's metadata never blocks the others.
            errors.append({"table": ".".join(identifier), "error": type(exc).__name__})
    return {"tables": published, "errors": errors}
