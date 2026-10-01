"""One compiled query in a disposable process: read-only, bounded, with only one catalog token.

Input on stdin (JSON): the catalog URI and warehouse, a read token of the team's automation
principal, the internal S3 endpoint, the datasets to bind, the SQL, its parameters and the row
limit. For every dataset the worker loads the Iceberg table once, with vended credentials that
Polaris scopes to that table, and reads that exact metadata file with `iceberg_scan`. DuckDB never
talks to the catalog, so a share recipient that cannot list namespaces still reads its tables,
and each answer names the snapshots it read.

Then DuckDB is locked: no local files, no extension loading, no setting changes. Errors never
leave the process with details: provider messages can contain tokens, SQL or row values.
"""

import datetime
import decimal
import json
import math
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

MAX_JS_INT = 2**53 - 1


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def connect(threads=2, memory="1GB"):
    import duckdb

    settings = {"threads": threads, "memory_limit": memory, "autoinstall_known_extensions": False,
                "autoload_known_extensions": False, "allow_community_extensions": False}
    if directory := os.environ.get("DUCKDB_EXTENSION_DIRECTORY"):
        settings["extension_directory"] = directory
    connection = duckdb.connect(config=settings)
    connection.execute("SET TimeZone = 'UTC'")  # Before lock(): dates in answers are UTC, as the models state.
    return connection


def load_tables(job):
    """loadTable with vended credentials for every binding; nothing else is asked of the catalog."""
    import httpx

    headers = {"Authorization": f"Bearer {job['token']}", "Polaris-Realm": "POLARIS"}
    with httpx.Client(timeout=20, trust_env=False, headers=headers) as client:
        response = client.get(f"{job['uri']}/v1/config", params={"warehouse": job["warehouse"]})
        response.raise_for_status()
        config = response.json()
        prefix = {**config.get("defaults", {}), **config.get("overrides", {})}.get("prefix", "")
        base = f"{job['uri']}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
        loaded = []
        for binding in job["bindings"]:
            namespace = quote(chr(31).join(binding["namespace"]), safe="")
            response = client.get(f"{base}/namespaces/{namespace}/tables/{quote(binding['table'], safe='')}",
                                  headers={"X-Iceberg-Access-Delegation": "vended-credentials"})
            response.raise_for_status()
            loaded.append((binding, response.json()))
        return loaded


def bind(connection, job, loaded):
    s3 = urlsplit(job["s3Endpoint"])
    if s3.scheme not in {"http", "https"} or not s3.netloc:
        raise ValueError("Invalid internal S3 endpoint")
    connection.execute("LOAD httpfs; LOAD avro; LOAD iceberg;")
    snapshots = {}
    for i, (binding, table) in enumerate(loaded):
        storage, metadata = table["config"], table["metadata"]
        location = metadata["location"].rstrip("/") + "/"
        connection.execute(f"""
            CREATE SECRET {identifier(f"ds_{i}")} (
                TYPE s3,
                KEY_ID {literal(storage["s3.access-key-id"])},
                SECRET {literal(storage["s3.secret-access-key"])},
                SESSION_TOKEN {literal(storage["s3.session-token"])},
                REGION {literal(storage.get("s3.region", "us-east-1"))},
                ENDPOINT {literal(s3.netloc)},
                URL_STYLE 'path', USE_SSL {str(s3.scheme == "https").lower()},
                SCOPE {literal(location)}
            )
        """)
        view = identifier(binding["view"])
        if metadata.get("current-snapshot-id") in (None, -1):
            # An empty table has no snapshot to scan; keep its columns so the query still plans.
            columns = next((s["fields"] for s in metadata.get("schemas", [])
                            if s.get("schema-id") == metadata.get("current-schema-id")), [])
            empty = ", ".join(f"NULL AS {identifier(c['name'])}" for c in columns) or "NULL AS empty"
            connection.execute(f"CREATE TEMP VIEW {view} AS SELECT {empty} WHERE false")
        else:
            source = literal(table["metadata-location"])
            connection.execute(f"CREATE TEMP VIEW {view} AS SELECT * FROM iceberg_scan({source})")
        current = metadata.get("current-snapshot-id")
        committed = next((s.get("timestamp-ms") for s in metadata.get("snapshots", [])
                          if s.get("snapshot-id") == current), None) if current not in (None, -1) else None
        snapshots[binding["view"]] = {"id": current, "committedAt": committed}
    return snapshots


def lock(connection):
    """After binding, the session can read the bound tables and nothing else."""
    # `enable_external_access = false` would also end the S3 reads the views need.
    connection.execute("SET disabled_filesystems = 'LocalFileSystem'")
    connection.execute("SET lock_configuration = true")


def plain(value):
    """A JSON value that a browser reads exactly: large integers and decimals stay text."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_JS_INT else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() and abs(value) < 10**15 else str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value[:64]).hex()
    return str(value)[:512]


def execute(connection, sql, params, limit):
    cursor = connection.execute(sql, params)
    columns = [{"name": d[0], "type": str(d[1])} for d in cursor.description]
    rows = cursor.fetchmany(limit + 1)
    return {"columns": columns, "rows": [[plain(v) for v in row] for row in rows[:limit]],
            "truncated": len(rows) > limit}


def run(job):
    connection = connect()
    try:
        snapshots = bind(connection, job, load_tables(job))
        lock(connection)
        return {**execute(connection, job["sql"], job["params"], job["limit"]), "snapshots": snapshots}
    finally:
        connection.close()


if __name__ == "__main__":
    import resource

    # DuckDB reserves far more address space than it uses; its memory_limit bounds real use.
    resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    try:
        # Under memory pressure the kernel ends this query, never the gateway serving everyone.
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass
    try:
        print(json.dumps(run(json.load(sys.stdin)), separators=(",", ":")))
    except Exception as exc:  # noqa: BLE001 - Provider errors can contain credentials and data.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(json.dumps({"error": "catalog", "status": status} if status else {"error": type(exc).__name__}))
        sys.exit(1)
