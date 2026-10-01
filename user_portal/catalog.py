"""Public catalog metadata projections. Provider config and credentials stay server-side."""

import json
import re
from urllib.parse import urlsplit

SENSITIVE = re.compile(
    r"secret|token|credential|password|access.key|private.key|authorization", re.IGNORECASE
)
PRODUCER = "iceberg-data-platform."
QUALITY = {"pass", "warn", "fail"}


def properties(values):
    return {str(k): str(v) for k, v in values.items() if not SENSITIVE.search(str(k))}


def producer(values, extensions):
    """The Bridge table-property conventions (contracts/bridge/v1/table-properties.md), made safe to show.

    Anyone with write access sets these, so every value is capped and typed, lineage entries are
    validated, and a link is kept only when its origin is a registered extension.
    """
    name = str(values.get(PRODUCER + "producer", "")).strip()
    if not name:
        return None

    def text(key, limit=200):
        value = values.get(PRODUCER + key)
        return str(value)[:limit] if value not in (None, "") else None

    link, extension = text("producer.url", 2000), None
    if link:
        parsed = urlsplit(link)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        extension = next((id for id, url in extensions.items() if url == origin), None)
        if parsed.scheme not in ("http", "https") or not extension:
            link = None
    inputs = []
    try:
        raw = json.loads(values.get(PRODUCER + "lineage.inputs") or "[]")
    except ValueError:
        raw = []
    for item in raw if isinstance(raw, list) else []:
        if (isinstance(item, dict) and isinstance(item.get("database"), str) and isinstance(item.get("name"), str)
                and isinstance(item.get("namespace"), list) and all(isinstance(p, str) for p in item["namespace"])):
            inputs.append({"database": item["database"][:256], "namespace": [p[:256] for p in item["namespace"][:20]],
                           "name": item["name"][:256]})
        if len(inputs) == 50:
            break
    status = text("quality.status", 10)
    return {
        "name": name[:40], "version": text("producer.version", 80), "extension": extension, "url": link,
        "runId": text("producer.run-id", 80), "runAt": text("producer.run-at", 40),
        "revision": text("producer.source-revision", 80), "node": text("producer.node", 300),
        "inputs": inputs, "inputsTruncated": values.get(PRODUCER + "lineage.inputs-truncated") == "true",
        "quality": {"status": status, "summary": text("quality.summary"), "checkedAt": text("quality.checked-at", 40)}
        if status in QUALITY else None,
    }


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


def object_details(kind, loaded, extensions=None):
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
        "comment": str(metadata.get("properties", {}).get("comment", ""))[:2000],
        "producer": producer(metadata.get("properties", {}), extensions or {}),
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


def text(value):
    """Ossie documents are user-written: show only scalars and JSON for anything nested."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def expressions(item):
    found = item.get("expression")
    if isinstance(found, dict):
        found = found.get("dialects")
    elif isinstance(found, str):
        return [{"dialect": "", "expression": found}]
    return [{"dialect": text(e.get("dialect")), "expression": text(e.get("expression"))} for e in dicts(found)]


def ossie_models(payload):
    """The semantic models in a stored document. Polaris keeps the model as a JSON string, and
    clients differ in whether it is one model, a list of models or a whole Ossie document."""
    if isinstance(payload, list):
        return [m for item in payload for m in ossie_models(item)]
    if not isinstance(payload, dict):
        return []
    if "datasets" in payload or "metrics" in payload:
        return [payload]
    return ossie_models(payload.get("semantic_model"))


def dataset_target(source, namespace):
    """The Iceberg table a dataset reads: `catalog.namespace….table`, `namespace.table` or `table`.

    Three or more parts start with the catalog, so every part between it and the table is a
    nested namespace level; one part means a table in the model's own namespace.
    """
    parts = [p for p in text(source).split(".") if p]
    if not parts:
        return None
    levels = parts[1:-1] if len(parts) > 2 else parts[:-1]
    return {"namespace": levels or list(namespace), "name": parts[-1]}


def context_rows(value):
    """AI context as label/value rows: Ossie uses an object such as {instructions, synonyms}."""
    if value in (None, "", [], {}):
        return []
    if not isinstance(value, dict):
        value = {"context": value}
    return [
        {"label": text(key).replace("_", " ").capitalize(),
         "value": ", ".join(text(v) for v in item) if isinstance(item, list) else text(item)}
        for key, item in value.items()
    ]


def semantic_model_details(name, namespace, loaded):
    document = loaded.get("document") or {}
    raw = document.get("semantic_model")
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
    models = []
    for model in ossie_models(payload):
        datasets = [
            {
                "name": text(d.get("name")),
                "description": text(d.get("description")),
                "source": text(d.get("source")),
                "table": dataset_target(d.get("source"), namespace),
                "primaryKey": [text(k) for k in d["primary_key"]] if isinstance(d.get("primary_key"), list) else [],
                "fields": [
                    {
                        "name": text(f.get("name")),
                        "description": text(f.get("description")),
                        "datatype": text(f.get("datatype")),
                        "dimension": bool(f.get("dimension")),
                        "expressions": expressions(f),
                    }
                    for f in dicts(d.get("fields"))
                ],
            }
            for d in dicts(model.get("datasets"))
        ]
        models.append(
            {
                "name": text(model.get("name")) or name,
                "description": text(model.get("description")),
                "aiContext": context_rows(model.get("ai_context")),
                "datasets": datasets,
                "relationships": [
                    {
                        "name": text(r.get("name")),
                        "from": text(r.get("from")),
                        "to": text(r.get("to")),
                        "fromColumns": [text(c) for c in r.get("from_columns") or []],
                        "toColumns": [text(c) for c in r.get("to_columns") or []],
                    }
                    for r in dicts(model.get("relationships"))
                ],
                "metrics": [
                    {
                        "name": text(m.get("name")),
                        "description": text(m.get("description")),
                        "datatype": text(m.get("datatype")),
                        "expressions": expressions(m),
                    }
                    for m in dicts(model.get("metrics"))
                ],
            }
        )
    return {
        "kind": "semantic-model",
        "name": name,
        "namespace": list(namespace),
        "specVersion": text(document.get("version")),
        "entityVersion": text(loaded.get("entity-version")),
        "models": models,
        # The stored text, pretty-printed when it is JSON, so users can copy exactly what Polaris holds.
        "definition": json.dumps(payload, indent=2, ensure_ascii=False) if payload is not None else text(raw),
    }
