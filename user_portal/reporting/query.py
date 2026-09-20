"""A deliberately small visual-query contract for the live reporting proof.

Only catalog column names and bound filter values enter the generated SQL. There
is no arbitrary SQL, YAML, filesystem source or connection configuration editor.
"""

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from user_portal.preview import connect, sql_identifier

MAX_GROUPS = 1000


class ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    measure: str = Field(min_length=1, max_length=256)
    timestamp: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=256)
    category_value: str | None = Field(default=None, max_length=256)
    grain: Literal["hour", "day", "week", "month"] = "day"


def load_table(source):
    """Resolve metadata and vend table credentials as the viewer, never the platform."""
    with httpx.Client(timeout=10, trust_env=False) as client:
        client.headers.update({"Authorization": f"Bearer {source['token']}", "Polaris-Realm": "POLARIS"})
        config = client.get(f"{source['uri']}/v1/config", params={"warehouse": source["database"]})
        config.raise_for_status()
        config = config.json()
        prefix = {**config.get("defaults", {}), **config.get("overrides", {})}.get("prefix", "")
        base = f"{source['uri']}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
        namespace = quote(chr(31).join(source["namespace"]), safe="")
        response = client.get(
            f"{base}/namespaces/{namespace}/tables/{quote(source['table'], safe='')}",
            headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
        )
        response.raise_for_status()
        return response.json()


def compile_queries(source, query):
    """The caller supplies a gateway-authorized, snapshot-pinned table reference."""
    reference = ".".join(
        sql_identifier(p) for p in ("lakehouse", ".".join(source["namespace"]), source["table"])
    )
    snapshot = source["snapshotId"]
    if snapshot is not None:
        if not str(snapshot).isdigit() or len(str(snapshot)) > 19:
            raise ValueError("Invalid snapshot")
        reference += f" AT (VERSION => {int(snapshot)})"
    predicates = ["true" if snapshot is not None else "false"]
    params = []
    if query.category_value is not None:
        predicates.append(f"{sql_identifier(query.category)} = ?")
        params.append(query.category_value)
    where = " AND ".join(predicates)
    measure = sql_identifier(query.measure)
    timestamp = sql_identifier(query.timestamp)
    category = sql_identifier(query.category)
    summary = f"SELECT count(*) AS records, sum({measure}) AS total FROM {reference} WHERE {where}"
    trend = (
        f"SELECT date_trunc('{query.grain}', {timestamp}) AS period, {category} AS category, "
        f"sum({measure}) AS total FROM {reference} WHERE {where} "
        f"GROUP BY 1, 2 ORDER BY 1, 2 LIMIT {MAX_GROUPS + 1}"
    )
    return summary, trend, params


def exact_value(value):
    """Keep exact integers and decimals in API data; chart values are a separate projection."""
    if isinstance(value, Decimal) or isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite report value")
    if isinstance(value, str) and len(value) > 512:
        raise ValueError("Category value is too long")
    return value


def read_report(source, query):
    loaded = load_table(source)
    metadata = loaded["metadata"]
    if source["tableUuid"] != metadata["table-uuid"]:
        raise ValueError("Table was replaced")
    if source["schemaId"] != metadata["current-schema-id"]:
        raise ValueError("Schema changed; refresh the report")
    snapshot = source["snapshotId"]
    if snapshot is not None and snapshot not in {
        str(s["snapshot-id"]) for s in metadata.get("snapshots", [])
    }:
        raise ValueError("Snapshot expired; refresh the report")
    fields = next(s["fields"] for s in metadata["schemas"] if s["schema-id"] == source["schemaId"])
    types = {f["name"]: f["type"] for f in fields if isinstance(f["type"], str)}
    measure_type = types.get(query.measure, "")
    if measure_type not in {"int", "long", "float", "double"} and not measure_type.startswith("decimal("):
        raise ValueError("Select a numeric measure")
    if types.get(query.timestamp) not in {
        "date",
        "timestamp",
        "timestamptz",
        "timestamp_ns",
        "timestamptz_ns",
    }:
        raise ValueError("Select a timestamp or date column")
    if types.get(query.category) != "string":
        raise ValueError("Select a text category")
    connection = connect(source, loaded)
    try:
        # UTC is explicit in this proof. A future UI timezone control must also
        # participate in cache keys and date grouping semantics.
        connection.execute("SET TimeZone = 'UTC'")
        summary_sql, trend_sql, params = compile_queries(source, query)
        records, total = connection.execute(summary_sql, params).fetchone()
        cursor = connection.execute(trend_sql, params)
        columns = [{"name": c[0], "type": str(c[1])} for c in cursor.description]
        rows = cursor.fetchmany(MAX_GROUPS + 1)
        if len(rows) > MAX_GROUPS:
            raise ValueError("Too many groups; select a coarser time grain or filter")
        return {
            "columns": columns,
            "rows": [[exact_value(v) for v in row] for row in rows],
            "records": exact_value(records),
            "total": exact_value(total),
            "snapshotId": snapshot,
            "tableUuid": source["tableUuid"],
            "schemaId": source["schemaId"],
            "timezone": "UTC",
        }
    finally:
        connection.close()
