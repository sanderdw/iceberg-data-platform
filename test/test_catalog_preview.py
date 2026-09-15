"""Preview resource boundary and real Iceberg scans against temporary local files."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pytest
from pyiceberg.catalog.noop import NoopCatalog
from pyiceberg.io.pyarrow import PyArrowFileIO
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
from pyiceberg.schema import Schema
from pyiceberg.table import CommitTableResponse, Table
from pyiceberg.table.metadata import new_table_metadata
from pyiceberg.table.sorting import UNSORTED_SORT_ORDER
from pyiceberg.table.update import update_table_metadata
from pyiceberg.types import LongType, NestedField, StringType

from server.models import ServiceError
from user_portal.catalog import columns
from user_portal.preview import read_preview, run_preview


def test_nested_schema_fields_keep_ids_and_types():
    result = columns(
        {
            "fields": [
                {
                    "id": 1,
                    "name": "items",
                    "type": {
                        "type": "list",
                        "element-id": 2,
                        "element": {
                            "type": "struct",
                            "fields": [
                                {"id": 3, "name": "value", "type": "decimal(10, 2)", "required": True}
                            ],
                        },
                    },
                }
            ]
        }
    )
    assert [(c["id"], c["name"], c["type"]) for c in result] == [
        (1, "items", "list"),
        (2, "items.element", "struct"),
        (3, "items.element.value", "decimal(10, 2)"),
    ]


def test_preview_process_does_not_inherit_platform_secrets(monkeypatch):
    monkeypatch.setenv("POLARIS_CLIENT_SECRET", "platform-secret")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"rows": []}'))
    monkeypatch.setattr(subprocess, "run", run)
    assert run_preview({"token": "user-token"}) == {"rows": []}
    args, kwargs = run.call_args
    assert "platform-secret" not in str(kwargs)
    assert "user-token" not in str(args)
    assert json.loads(kwargs["input"])["token"] == "user-token"
    assert kwargs["timeout"] == 30
    run.return_value = SimpleNamespace(returncode=1, stdout="provider secret", stderr="storage secret")
    with pytest.raises(ServiceError, match="Preview unavailable"):
        run_preview({})
    run.side_effect = subprocess.TimeoutExpired("preview", 30)
    with pytest.raises(ServiceError, match="30 seconds"):
        run_preview({})


def test_real_preview_is_bounded_and_reads_selected_snapshot(tmp_path, monkeypatch):
    catalog = NoopCatalog("preview")
    data = pa.table({"id": list(range(150)), "event": ["x" * 600] * 149 + [None]})
    table = Table(
        identifier=("analytics", "events"),
        catalog=catalog,
        io=PyArrowFileIO(),
        metadata_location=str(tmp_path / "metadata.json"),
        metadata=new_table_metadata(
            Schema(NestedField(1, "id", LongType()), NestedField(2, "event", StringType())),
            UNPARTITIONED_PARTITION_SPEC,
            UNSORTED_SORT_ORDER,
            str(tmp_path),
        ),
    )
    monkeypatch.setattr(catalog, "load_table", lambda _: table)
    monkeypatch.setattr(
        catalog,
        "commit_table",
        lambda t, requirements, updates: CommitTableResponse(
            metadata=update_table_metadata(t.metadata, updates), metadata_location=t.metadata_location
        ),
    )
    table.append(data)
    old = str(table.current_snapshot().snapshot_id)
    table.overwrite(pa.table({"id": [999], "event": ["new"]}))
    monkeypatch.setattr("pyiceberg.catalog.load_catalog", lambda *a, **kw: catalog)
    request = {
        "database": "preview",
        "uri": "http://catalog",
        "token": "user-token",
        "namespace": ["analytics"],
        "table": "events",
        "s3Endpoint": "http://rustfs:9000",
        "snapshotId": old,
        "limit": 100,
    }
    result = read_preview(request)
    assert len(result["rows"]) == 100
    assert result["rows"][0][0] == "0" and len(result["rows"][0][1]) == 513
    assert result["cellsTruncated"] and result["snapshotId"] == old
    result = read_preview({**request, "snapshotId": str(table.current_snapshot().snapshot_id)})
    assert result["rows"] == [["999", "new"]]
    assert read_preview({**request, "snapshotId": None})["rows"] == []
    with pytest.raises(ValueError, match="expired"):
        read_preview({**request, "snapshotId": "123"})
