"""Public catalog metadata projections. Provider config and credentials stay server-side."""

import re

SENSITIVE = re.compile(
    r"secret|token|credential|password|access.key|private.key|authorization", re.IGNORECASE
)


def properties(values):
    return {str(k): str(v) for k, v in values.items() if not SENSITIVE.search(str(k))}


def identifier(value):
    return str(value) if value is not None else None


def columns(schema):
    result = []

    def visit(fields, prefix=""):
        for field in fields:
            name = prefix + field["name"]
            kind = field["type"]
            result.append(
                {
                    "id": field["id"],
                    "name": name,
                    "type": kind if isinstance(kind, str) else kind["type"],
                    "required": field.get("required", False),
                    "doc": field.get("doc", ""),
                }
            )
            if isinstance(kind, dict):
                if kind["type"] == "struct":
                    visit(kind["fields"], name + ".")
                elif kind["type"] == "list":
                    visit(
                        [
                            {
                                "name": "element",
                                "id": kind["element-id"],
                                "type": kind["element"],
                                "required": kind.get("element-required", False),
                            }
                        ],
                        name + ".",
                    )
                elif kind["type"] == "map":
                    visit(
                        [
                            {
                                "name": part,
                                "id": kind[f"{part}-id"],
                                "type": kind[part],
                                "required": part == "key" or kind.get("value-required", False),
                            }
                            for part in ("key", "value")
                        ],
                        name + ".",
                    )

    visit(schema.get("fields", []))
    return result


def object_details(kind, loaded):
    metadata = loaded["metadata"]
    schemas = metadata.get("schemas", [])
    versions = metadata.get("versions", [])
    version = next((v for v in versions if v["version-id"] == metadata.get("current-version-id")), {})
    schema_id = metadata.get("current-schema-id") if kind == "table" else version.get("schema-id")
    schema = next((s for s in schemas if s["schema-id"] == schema_id), {})
    result = {
        "kind": kind,
        "uuid": metadata.get(f"{kind}-uuid"),
        "location": metadata.get("location"),
        "formatVersion": metadata.get("format-version"),
        "schemaId": schema_id,
        "columns": columns(schema),
        "properties": properties(metadata.get("properties", {})),
    }
    if kind == "view":
        result.update(
            {
                "currentVersionId": metadata.get("current-version-id"),
                "versions": [
                    {
                        "id": v["version-id"],
                        "timestamp": v.get("timestamp-ms"),
                        "schemaId": v.get("schema-id"),
                        "defaultNamespace": v.get("default-namespace", []),
                        "defaultCatalog": v.get("default-catalog"),
                        "representations": [
                            {"dialect": r.get("dialect"), "sql": r.get("sql", "")}
                            for r in v.get("representations", [])
                            if r.get("type") == "sql"
                        ],
                    }
                    for v in reversed(versions)
                ],
            }
        )
        return result
    result.update(
        {
            "updatedAt": metadata.get("last-updated-ms"),
            "currentSnapshotId": identifier(metadata.get("current-snapshot-id"))
            if metadata.get("current-snapshot-id") != -1
            else None,
            "snapshots": [
                {
                    "id": str(s["snapshot-id"]),
                    "parentId": identifier(s.get("parent-snapshot-id")),
                    "timestamp": s.get("timestamp-ms"),
                    "schemaId": s.get("schema-id"),
                    "summary": {
                        k: str(v)
                        for k, v in s.get("summary", {}).items()
                        if k == "operation" or k.startswith(("total-", "added-", "deleted-", "removed-"))
                    },
                }
                for s in reversed(metadata.get("snapshots", []))
            ],
            "history": [
                {"snapshotId": str(h["snapshot-id"]), "timestamp": h["timestamp-ms"]}
                for h in reversed(metadata.get("snapshot-log", []))
            ],
            "refs": [
                {
                    "name": name,
                    "snapshotId": str(ref["snapshot-id"]),
                    "type": ref["type"],
                    "minSnapshotsToKeep": ref.get("min-snapshots-to-keep"),
                    "maxSnapshotAgeMs": ref.get("max-snapshot-age-ms"),
                    "maxRefAgeMs": ref.get("max-ref-age-ms"),
                }
                for name, ref in metadata.get("refs", {}).items()
            ],
            "partitionSpecs": metadata.get("partition-specs", []),
            "defaultSpecId": metadata.get("default-spec-id"),
            "sortOrders": metadata.get("sort-orders", []),
            "defaultSortOrderId": metadata.get("default-sort-order-id"),
        }
    )
    return result
