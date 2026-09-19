"""Bounded, read-only previews in disposable processes with only user credentials."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from server.models import ServiceError


def run_preview(request):
    with tempfile.TemporaryDirectory(prefix="iceberg-preview-") as home:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "user_portal.preview"],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=home,
                # Never inherit platform credentials or a user's local catalog config.
                env={
                    "PATH": os.defpath,
                    "HOME": home,
                    "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    # Extensions are installed at image build; a preview never downloads them.
                    "DUCKDB_EXTENSION_DIRECTORY": os.environ.get(
                        "DUCKDB_EXTENSION_DIRECTORY", str(Path.home() / ".duckdb" / "extensions")
                    ),
                },
            )
        except subprocess.TimeoutExpired:
            raise ServiceError(504, "Preview exceeded 30 seconds. Use marimo for larger reads.") from None
    if completed.returncode or len(completed.stdout) > 4_000_000:
        raise ServiceError(502, "Preview unavailable. Check read permissions or use marimo for this table.")
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise ServiceError(502, "Preview could not be read.") from None


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def sql_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def connect(request, metadata):
    """Attach the catalog read-only with the user's token and the table's vended credentials."""
    import duckdb

    storage, location = metadata["config"], metadata["metadata"]["location"].rstrip("/") + "/"
    s3 = urlsplit(request["s3Endpoint"])
    if s3.scheme not in {"http", "https"} or not s3.netloc:
        raise ValueError("Invalid internal S3 endpoint")
    # DuckDB's own limit bounds real memory; RLIMIT_AS below only caps address space.
    settings = {"threads": 2, "memory_limit": "512MB", "temp_directory": os.getcwd()}
    if directory := os.environ.get("DUCKDB_EXTENSION_DIRECTORY"):
        settings["extension_directory"] = directory
    connection = duckdb.connect(config=settings)
    connection.execute("LOAD httpfs; LOAD iceberg;")
    connection.execute(f"CREATE SECRET catalog (TYPE iceberg, TOKEN {sql_literal(request['token'])})")
    connection.execute(f"""
        CREATE SECRET table_storage (
            TYPE s3,
            KEY_ID {sql_literal(storage["s3.access-key-id"])},
            SECRET {sql_literal(storage["s3.secret-access-key"])},
            SESSION_TOKEN {sql_literal(storage["s3.session-token"])},
            REGION {sql_literal(storage.get("s3.region", "us-east-1"))},
            ENDPOINT {sql_literal(s3.netloc)},
            URL_STYLE 'path', USE_SSL {str(s3.scheme == "https").lower()},
            SCOPE {sql_literal(location)}
        )
    """)
    # No automatic vending: it would replace the internal S3 endpoint with the
    # host-facing one. The credentials above are vended for this user and table.
    connection.execute(f"""
        ATTACH {sql_literal(request["database"])} AS lakehouse (
            TYPE iceberg, ENDPOINT {sql_literal(request["uri"])}, SECRET catalog,
            ACCESS_DELEGATION_MODE 'none', SUPPORT_NESTED_NAMESPACES true, READ_ONLY
        )
    """)
    return connection


def as_text(name, kind):
    column = sql_identifier(name)
    if kind == "VARIANT":
        # A variant reads best as JSON; its plain text form leaves strings unquoted.
        return f"CASE WHEN {column} IS NOT NULL THEN CAST(CAST({column} AS JSON) AS VARCHAR) END"
    return f"CAST({column} AS VARCHAR)"


def read_rows(connection, source, limit):
    """Up to `limit` rows of at most 50 columns, rendered as text by DuckDB.

    Casting in SQL keeps every type readable, including Iceberg v3 variant, geometry
    and nanosecond timestamps, and keeps large integers exact.
    """
    described = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
    names = [row[0] for row in described]
    shown = names[:50]
    cells = ", ".join(f"left({as_text(name, kind)}, 513)" for name, kind, *_ in described[:50])
    rows, truncated = [], False
    if limit:
        for row in connection.execute(f"SELECT {cells} FROM {source} LIMIT {int(limit)}").fetchall():
            values = []
            for value in row:
                if value is not None and len(value) > 512:
                    value, truncated = value[:512] + "…", True
                values.append(value)
            rows.append(values)
    return shown, rows, len(names) > 50, truncated


def read_preview(request):
    import httpx

    with httpx.Client(timeout=20, trust_env=False) as client:
        client.headers.update({"Authorization": f"Bearer {request['token']}", "Polaris-Realm": "POLARIS"})
        response = client.get(f"{request['uri']}/v1/config", params={"warehouse": request["database"]})
        response.raise_for_status()
        config = response.json()
        prefix = {**config.get("defaults", {}), **config.get("overrides", {})}.get("prefix", "")
        base = f"{request['uri']}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
        namespace = quote(chr(31).join(request["namespace"]), safe="")
        response = client.get(
            f"{base}/namespaces/{namespace}/tables/{quote(request['table'], safe='')}",
            headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
        )
        response.raise_for_status()
        metadata = response.json()
    snapshot_id = request["snapshotId"]
    snapshots = {str(s["snapshot-id"]) for s in metadata["metadata"].get("snapshots", [])}
    if snapshot_id is not None and snapshot_id not in snapshots:
        raise ValueError("Snapshot expired")
    connection = connect(request, metadata)
    try:
        # DuckDB represents nested Iceberg namespaces as a single dotted schema name.
        source = ".".join(
            sql_identifier(part) for part in ("lakehouse", ".".join(request["namespace"]), request["table"])
        )
        # An empty table remains empty even if a concurrent commit arrives after preflight.
        if snapshot_id is not None:
            source += f" AT (VERSION => {int(snapshot_id)})"
        names, rows, columns_truncated, cells_truncated = read_rows(
            connection, source, request["limit"] if snapshot_id is not None else 0
        )
    finally:
        connection.close()
    return {
        "columns": names,
        "rows": rows,
        "snapshotId": snapshot_id,
        "limit": request["limit"],
        "columnsTruncated": columns_truncated,
        "cellsTruncated": cells_truncated,
    }


if __name__ == "__main__":
    import resource

    # DuckDB reserves far more address space than it uses and cannot start below
    # 3 GiB; its memory_limit of 512 MB is the bound on memory actually used.
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        # Under memory pressure the kernel must end this preview, never the portal
        # that shares its container and serves the other users.
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass  # Not Linux; the limits above still apply.
    try:
        print(json.dumps(read_preview(json.load(sys.stdin))))
    except Exception:  # noqa: BLE001 - Provider errors can contain credentials and data.
        # Never emit provider exceptions, SQL, tokens, storage keys or row values on failure.
        sys.exit(1)
