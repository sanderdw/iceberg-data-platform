"""Pure mapping from a Bridge listing to OpenMetadata 2.0 create requests.

Names are stable across renames on the platform side: a database is named by its id, a data
product by its share id. Display names carry what people read.

| Platform | OpenMetadata |
| --- | --- |
| Environment | DatabaseService `<prefix>-<environment>`, type CustomDatabase |
| Database `db-…` | Database `db-…` |
| Namespace | DatabaseSchema, parts joined with `.` |
| Table / view | Table, tableType Iceberg / View, no view SQL |
| Semantic model metric | Metric `<prefix>__<db>__<namespace…>__<model>__<metric>` |
| Listed share | DataProduct `<prefix>-<share id>` in the configured domain |
"""

import hashlib
import re

ENVIRONMENTS = ("development", "acceptance", "production")
PRIMITIVES = {
    "boolean": "BOOLEAN", "int": "INT", "long": "BIGINT", "float": "FLOAT", "double": "DOUBLE", "date": "DATE",
    "time": "TIME", "timestamp": "TIMESTAMP", "timestamp_ns": "TIMESTAMP", "timestamptz": "TIMESTAMPZ",
    "timestamptz_ns": "TIMESTAMPZ", "string": "STRING", "uuid": "UUID", "binary": "BYTES", "variant": "VARIANT",
    "geometry": "GEOMETRY", "geography": "GEOGRAPHY",
}
TIME_TRANSFORMS = {"year", "month", "day", "hour"}
AGGREGATES = {"SUM": "SUM", "COUNT": "COUNT", "AVG": "AVERAGE", "MIN": "MIN", "MAX": "MAX", "MEDIAN": "MEDIAN"}
CONTROL = re.compile(r"[\x00-\x1f]")


def clean(name):
    """An OpenMetadata entity name: no `::`, `>`, `"` or control characters, at most 256 characters."""
    value = CONTROL.sub("", str(name)).replace('"', "'").replace(">", ")").replace("::", ":")
    if len(value) > 256:
        value = value[:239] + "-" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return value or "-"


def quote(name):
    """One part of a fully qualified name: OpenMetadata quotes a part that contains a dot."""
    return f'"{name}"' if "." in name else name


def fqn(*parts):
    return ".".join(quote(clean(p)) for p in parts)


def service_name(prefix, environment):
    return clean(f"{prefix}-{environment}")


def schema_name(namespace):
    return clean(".".join(namespace))


def database_fqn(prefix, listing):
    return fqn(service_name(prefix, listing["database"]["environment"]), listing["database"]["id"])


def schema_fqn(prefix, listing, namespace):
    return database_fqn(prefix, listing) + "." + quote(schema_name(namespace))


def table_fqn(prefix, listing, namespace, name):
    return schema_fqn(prefix, listing, namespace) + "." + quote(clean(name))


def data_product_name(prefix, listing):
    return clean(f"{prefix}-{listing['id']}")


def metric_name(prefix, listing, model_object, model, metric):
    parts = [prefix, listing["database"]["id"], *model_object["namespace"], model_object["name"]]
    if model is not None:
        parts.append(model)
    return clean("__".join([*parts, metric]))


def key(namespace, name):
    return (tuple(namespace), name)


# Columns


def data_type(text):
    """OpenMetadata type fields for an Iceberg type as the Bridge writes it, such as `decimal(10,2)`."""
    text = text.strip()
    if text.startswith("struct<"):
        return {"dataType": "STRUCT"}
    if text.startswith("list<"):
        return {"dataType": "ARRAY", "arrayDataType": data_type(text[5:-1])["dataType"]}
    if text.startswith("map<"):
        return {"dataType": "MAP"}
    if match := re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", text):
        return {"dataType": "DECIMAL", "precision": int(match[1]), "scale": int(match[2])}
    if match := re.fullmatch(r"fixed\[(\d+)\]", text):
        return {"dataType": "FIXED", "dataLength": int(match[1])}
    return {"dataType": PRIMITIVES.get(text, "UNKNOWN")}


def column(c):
    result = {
        "name": clean(c["name"]),
        **data_type(c["type"]),
        "dataTypeDisplay": c["type"][:1000],
        **({"description": c["doc"]} if c.get("doc") else {}),
        **({"constraint": "NOT_NULL"} if c.get("required") else {}),
    }
    children = c.get("children") or []
    if result["dataType"] in ("STRUCT", "MAP"):
        result["children"] = [column(child) for child in children]
    elif result["dataType"] == "ARRAY" and children and children[0].get("children"):
        # A list of structs: OpenMetadata shows the element's fields under the column.
        result["children"] = [column(child) for child in children[0]["children"]]
    return result


def partition(fields):
    columns = []
    for f in fields:
        transform = f["transform"]
        if transform == "void" or not f.get("sourceColumn"):
            continue
        if transform == "identity":
            columns.append({"columnName": clean(f["sourceColumn"]), "intervalType": "COLUMN-VALUE"})
        elif transform in TIME_TRANSFORMS:
            columns.append({"columnName": clean(f["sourceColumn"]), "intervalType": "TIME-UNIT", "interval": transform})
        else:
            columns.append({"columnName": clean(f["sourceColumn"]), "intervalType": "OTHER", "interval": transform})
    return {"columns": columns} if columns else None


# Requests


def domain_request(domain):
    return {"name": clean(domain), "domainType": "Source-aligned",
            "description": "Data products of the Iceberg Data Platform, listed by their owning teams. "
                           "Listings carry metadata only; ask the owning team for a data share to read the data."}


def team_request(owner):
    return {"name": clean(owner["team"]), "displayName": owner["teamName"] or owner["team"], "teamType": "Group",
            "description": "A team of the Iceberg Data Platform."}


def service_request(prefix, environment, domain):
    return {"name": service_name(prefix, environment), "serviceType": "CustomDatabase",
            "displayName": f"Iceberg Data Platform ({environment})", "domains": [clean(domain)],
            "description": f"Listed data products of the Iceberg Data Platform's {environment} environment. "
                           "Published by the platform's OpenMetadata extension; metadata only."}


def scoped(domain):
    """The domain, set on every entity: OpenMetadata refuses a PATCH of an asset of a data product whose
    domain is only inherited."""
    return {"domains": [clean(domain)]} if domain else {}


def database_request(prefix, listing, owners, domain=None):
    database, owner = listing["database"], listing["owner"]
    return {"name": clean(database["id"]), "displayName": database["name"], **scoped(domain),
            "service": service_name(prefix, database["environment"]), "owners": owners,
            "description": f"Database {database['name']} of team {owner['teamName'] or owner['team']}."}


def schema_request(prefix, listing, namespace, owners, domain=None):
    return {"name": schema_name(namespace), "database": database_fqn(prefix, listing), "owners": owners,
            **scoped(domain)}


def table_request(prefix, listing, obj, owners, constraints=(), semantics=None, domain=None):
    """`semantics`: what the listed semantic models say about this table and its columns, with
    their tag labels (`semantics.enrich`)."""
    semantics = semantics or {"notes": [], "tags": [], "columns": {}}
    columns = []
    for c in obj["columns"]:
        result = column(c)
        if found := semantics["columns"].get(c["name"]):
            if not c.get("doc") and any(found["notes"]):
                result["description"] = "\n\n".join(n for n in found["notes"] if n)
            result["tags"] = list(found["tags"])
        columns.append(result)
    body = {
        "name": clean(obj["name"]),
        "tableType": "View" if obj["kind"] == "view" else "Iceberg",
        "columns": columns,
        "databaseSchema": schema_fqn(prefix, listing, obj["namespace"]),
        "owners": owners,
        **scoped(domain),
    }
    if description := "\n\n".join(filter(None, [obj.get("comment"), *semantics["notes"]])):
        body["description"] = description
    if semantics["tags"]:
        body["tags"] = list(semantics["tags"])
    if obj["kind"] == "table" and (spec := partition(obj.get("partitionFields", []))):
        body["tablePartition"] = spec
    if constraints:
        body["tableConstraints"] = list(constraints)
    return body


def expression(item):
    found = item.get("expressions") or []
    ansi = (e for e in found if e["dialect"].upper() in ("ANSI_SQL", "ANSI", "SQL"))
    chosen = next(ansi, found[0] if found else None)
    return chosen["expression"] if chosen else ""


def closing(text, start):
    """The index of the parenthesis that closes the one at `start`, or None."""
    depth = 0
    for index in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        if depth == 0:
            return index
    return None


def metric_type(code):
    """From the outermost aggregate of a SQL expression, which may have a FILTER clause and a constant
    scale such as `/ 4.0`; PERCENTAGE for `100 * … / …`; RATIO for another division; else OTHER."""
    text = code.strip()
    if re.match(r"^100(?:\.0*)?\s*\*", text) and "/" in text:
        return "PERCENTAGE"
    match = re.match(r"^([A-Za-z_]+)\s*\(", text)
    if match and match[1].upper() in AGGREGATES and (end := closing(text, match.end() - 1)) is not None:
        rest = text[end + 1:].strip()
        if (found := re.match(r"^FILTER\s*\(", rest, re.IGNORECASE)) and \
                (close := closing(rest, found.end() - 1)) is not None:
            rest = rest[close + 1:].strip()
        if not rest or re.fullmatch(r"[*/]\s*\d+(?:\.\d+)?", rest):
            return AGGREGATES[match[1].upper()]
    return "RATIO" if "/" in text else "OTHER"


def model_tables(listing, model):
    """The listed tables a semantic model's datasets read."""
    listed = {key(o["namespace"], o["name"]) for o in listing["objects"] if o["kind"] in ("table", "view")}
    found = []
    for dataset in model["datasets"]:
        if dataset.get("table") and (k := key(dataset["table"]["namespace"], dataset["table"]["name"])) in listed:
            if k not in found:
                found.append(k)
    return found


def metric_requests(prefix, listing, obj, domain, owners):
    """[(name, request, table keys)] for every metric of a listed semantic model."""
    result = []
    several = len(obj["models"]) > 1
    for model in obj["models"]:
        dimensions = [
            {"name": f"{d['name']}.{f['name']}"[:256], "type": "TIME" if f["timeDimension"] else "CATEGORICAL",
             **({"description": f["description"]} if f["description"] else {}),
             **({"expression": expression(f)} if expression(f) else {})}
            for d in model["datasets"] for f in d["fields"] if f["dimension"]
        ]
        tables = model_tables(listing, model)
        for metric in model["metrics"]:
            name = metric_name(prefix, listing, obj, model["name"] if several else None, metric["name"])
            code = expression(metric)
            lines = [metric["description"]] if metric["description"] else []
            if metric["synonyms"]:
                lines.append("Also called: " + ", ".join(metric["synonyms"]) + ".")
            lines += [f"{r['label']}: {r['value']}" for r in metric.get("aiContext", [])]
            lines.append(f"From semantic model {model['name']} in database {listing['database']['name']} "
                         f"({listing['database']['environment']}).")
            body = {
                "name": name, "displayName": metric["name"], "description": "\n\n".join(lines),
                "metricType": metric_type(code) if code else "OTHER",
                "domains": [clean(domain)], "owners": owners,
                **({"metricExpression": {"language": "SQL", "code": code}} if code else {}),
                **({"dimensions": dimensions} if dimensions else {}),
            }
            result.append((name, body, tables))
    return result


def constraints(prefix, listing):
    """Primary and foreign keys that the listed semantic models declare, on listed tables only.

    A key is kept only when its columns are top-level columns of the table, since Ossie fields may
    be expressions rather than columns.
    """
    tables = {key(o["namespace"], o["name"]): o for o in listing["objects"] if o["kind"] == "table"}
    found = {}

    def columns_of(k):
        return {c["name"] for c in tables[k]["columns"]}

    def add(k, constraint):
        if constraint not in found.setdefault(k, []):
            found[k].append(constraint)

    for obj in (o for o in listing["objects"] if o["kind"] == "semantic-model"):
        for model in obj["models"]:
            datasets = {d["name"]: d for d in model["datasets"] if d.get("table")}
            for d in datasets.values():
                k = key(d["table"]["namespace"], d["table"]["name"])
                if k in tables and d["primaryKey"] and set(d["primaryKey"]) <= columns_of(k):
                    add(k, {"constraintType": "PRIMARY_KEY", "columns": [clean(c) for c in d["primaryKey"]]})
            for r in model["relationships"]:
                source, target = datasets.get(r["from"]), datasets.get(r["to"])
                if not source or not target or len(r["fromColumns"]) != len(r["toColumns"]) or not r["fromColumns"]:
                    continue
                fk, pk = (key(x["table"]["namespace"], x["table"]["name"]) for x in (source, target))
                if fk in tables and pk in tables and set(r["fromColumns"]) <= columns_of(fk) \
                        and set(r["toColumns"]) <= columns_of(pk):
                    target_fqn = table_fqn(prefix, listing, *pk)
                    add(fk, {"constraintType": "FOREIGN_KEY", "columns": [clean(c) for c in r["fromColumns"]],
                             "referredColumns": [f"{target_fqn}.{quote(clean(c))}" for c in r["toColumns"]]})
    return found


def foreign_key_targets(prefix, listing, found):
    """The listed tables that each table's foreign keys refer to, by key."""
    tables = {table_fqn(prefix, listing, o["namespace"], o["name"]): key(o["namespace"], o["name"])
              for o in listing["objects"] if o["kind"] == "table"}
    return {
        k: {tables[fqn] for c in items if c["constraintType"] == "FOREIGN_KEY"
            for referred in c["referredColumns"] for fqn in tables if referred.startswith(fqn + ".")}
        for k, items in found.items()
    }


def data_product_request(prefix, listing, domain, owners, diagram="", models=""):
    owner = listing["owner"]["teamName"] or listing["owner"]["team"]
    database = listing["database"]
    lines = [listing["description"]] if listing["description"] else []
    lines.append(f"Owned by team **{owner}**, database {database['name']} ({database['environment']}).")
    if listing["contact"]:
        lines.append(f"Contact for access: {listing['contact']}.")
    lines.append("This listing shows metadata only. To read the data, ask the owning team for a data share.")
    if listing.get("expiresAt"):
        lines.append(f"Listed until {listing['expiresAt'][:10]}.")
    if models:
        lines.append(models)
    if diagram:
        lines.append(diagram)
    return {"name": data_product_name(prefix, listing), "displayName": listing["name"],
            "description": "\n\n".join(lines), "domains": [clean(domain)], "owners": owners}


def lineage_inputs(prefix, listing):
    """(upstream key, downstream key) pairs between listed tables, from the producer conventions."""
    listed = {key(o["namespace"], o["name"]) for o in listing["objects"] if o["kind"] in ("table", "view")}
    edges = []
    for obj in (o for o in listing["objects"] if o["kind"] == "table" and o.get("producer")):
        for item in obj["producer"].get("inputs", []):
            upstream = key(item["namespace"], item["name"])
            if item["database"] == listing["database"]["id"] and upstream in listed \
                    and upstream != key(obj["namespace"], obj["name"]):
                edges.append((upstream, key(obj["namespace"], obj["name"])))
    return edges


def wanted(prefix, listings):
    """Everything this extension should keep in OpenMetadata for these listings."""
    result = {"databases": set(), "schemas": set(), "tables": set(), "metrics": set(), "dataProducts": set()}
    for listing in listings:
        result["databases"].add(database_fqn(prefix, listing))
        result["dataProducts"].add(data_product_name(prefix, listing))
        for obj in listing["objects"]:
            result["schemas"].add(schema_fqn(prefix, listing, obj["namespace"]))
            if obj["kind"] in ("table", "view"):
                result["tables"].add(table_fqn(prefix, listing, obj["namespace"], obj["name"]))
            else:
                result["metrics"].update(name for name, _, _ in metric_requests(prefix, listing, obj, "", []))
    return result
