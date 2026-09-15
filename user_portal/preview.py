"""Bounded, read-only previews in disposable processes with only user credentials."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
                    "PYICEBERG_HOME": home,
                    "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "ARROW_IO_THREADS": "2",
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


def read_preview(request):
    import pyarrow as pa
    from pyiceberg.catalog import load_catalog
    from pyiceberg.io.pyarrow import PyArrowFileIO

    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    catalog = load_catalog(
        request["database"],
        type="rest",
        uri=request["uri"],
        warehouse=request["database"],
        token=request["token"],
        **{
            "header.Polaris-Realm": "POLARIS",
            "header.X-Iceberg-Access-Delegation": "vended-credentials",
            "oauth2-server-uri": request["uri"] + "/v1/oauth/tokens",
        },
    )
    table = catalog.load_table((*request["namespace"], request["table"]))
    # Override host-facing endpoint only; keep the user's vended table credentials.
    table.io = PyArrowFileIO({**table.io.properties, "s3.endpoint": request["s3Endpoint"]})
    snapshot_id = request["snapshotId"]
    schema = table.schema()
    if snapshot_id is not None:
        snapshot = table.snapshot_by_id(int(snapshot_id))
        if snapshot is None:
            raise ValueError("Snapshot expired")
        if snapshot.schema_id is not None:
            schema = table.schemas()[snapshot.schema_id]
    names = [field.name for field in schema.fields[:50]]
    rows = []
    truncated = False
    # An empty table remains empty even if a concurrent commit arrives after preflight.
    if snapshot_id is not None:
        scan = table.scan(snapshot_id=int(snapshot_id), selected_fields=tuple(names), limit=request["limit"])
        with scan.to_arrow_batch_reader() as reader:
            for batch in reader:
                for row in batch.slice(0, request["limit"] - len(rows)).to_pylist():
                    values = []
                    for name in names:
                        value = row[name]
                        if value is not None:
                            value = str(value)
                            if len(value) > 512:
                                value = value[:512] + "…"
                                truncated = True
                        values.append(value)
                    rows.append(values)
                if len(rows) >= request["limit"]:
                    break
    return {
        "columns": names,
        "rows": rows,
        "snapshotId": snapshot_id,
        "limit": request["limit"],
        "columnsTruncated": len(schema.fields) > 50,
        "cellsTruncated": truncated,
    }


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        print(json.dumps(read_preview(json.load(sys.stdin))))
    except Exception:  # noqa: BLE001 - Provider errors can contain credentials and data.
        # Never emit provider exceptions, SQL, tokens, storage keys or row values on failure.
        sys.exit(1)
