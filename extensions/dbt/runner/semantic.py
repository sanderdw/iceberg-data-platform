"""Publish the project's semantic models after a successful build, so they live in Git with the pipeline.

A model is an Apache Ossie 0.3.0.dev0 document at `semantic_models/<database>/<namespace…>/<name>.yml`: the
first folder is the database by its display name with '-' as '_' (as in `+catalog_name`), the
folders after it are the namespace levels, and the file name is the model's name. Each one is
stored in Polaris, beside the tables, with the run's own write token, in the run's environment.
The file wins: a model changed elsewhere is replaced on the next build, and an unchanged one is
left alone so its version only moves when the file does. Removing a file does not delete a model.
Dataset and field descriptions the file leaves out come from the dbt descriptions of the models
it reads, as the user portal does with table comments and column docs.
"""

import json
import re
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

FOLDER = "semantic_models"
SPEC_VERSION = "0.3.0.dev0"
# The expression the platform runs, in order of preference; the portable `sql` is OSSIE_SQL_2026.
DIALECTS = ("DUCKDB", "OSSIE_SQL_2026", "ANSI_SQL")
MAX_DOCUMENT = 1_000_000
NAME = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class Refused(Exception):
    pass


def found(project):
    """(path, database, namespace parts, name) of every model file in the project."""
    root = Path(project) / FOLDER
    if not root.is_dir():
        return []
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".yml", ".yaml"):
            database, *namespace = path.relative_to(root).parent.parts or ("",)
            files.append((path, database, namespace, path.stem))
    return files


def model_of(payload):
    """The Apache Ossie 0.3.0.dev0 document in a file: one model at the root, with this version."""
    if isinstance(payload, dict) and "semantic_model" in payload:
        raise Refused(f"Apache Ossie {SPEC_VERSION} has no semantic_model list: put the model's name, datasets, "
                      "relationships and metrics at the root of the file")
    if not isinstance(payload, dict) or not payload.get("datasets"):
        raise Refused("the file is not an Apache Ossie semantic model with datasets")
    if payload.get("version", SPEC_VERSION) != SPEC_VERSION:
        raise Refused(f"the file declares Ossie version {str(payload['version'])[:40]!r}; write {SPEC_VERSION!r}")
    return {"version": SPEC_VERSION, **{k: v for k, v in payload.items() if k != "version"}}


def sql_of(item):
    """The expression of a field that the platform runs: a DUCKDB dialect, the portable sql, or ANSI_SQL."""
    expression = item.get("expression") if isinstance(item, dict) else None
    if not isinstance(expression, dict):
        return None
    options = {}
    for dialect in expression.get("dialects") or []:
        if isinstance(dialect, dict) and isinstance(dialect.get("dialect"), str):
            options.setdefault(dialect["dialect"].upper(), dialect.get("expression"))
    if expression.get("sql"):
        options["OSSIE_SQL_2026"] = expression["sql"]
    return next((options[d] for d in DIALECTS if options.get(d)), None)


def dbt_docs(target):
    """{(database, namespace parts, table): {"comment": str, "columns": {column: description}}} of this project."""
    from runner import publish

    nodes, columns, _, _ = publish.load(target)
    return {(node["database_name"], tuple(node["schema_name"].split(".")),
             node["identifier"] or node["alias"] or node["name"]):
            {"comment": node.get("description") or "", "columns": columns.get(id, {})}
            for id, node in nodes.items() if node["kind"] in publish.MATERIALIZED}


def table_of(source):
    """(namespace parts, table) a dataset reads: its source is `namespace….table`, as Polaris resolves it."""
    parts = str(source or "").split(".")
    return (tuple(parts[:-1]), parts[-1]) if len(parts) >= 2 and all(parts) else (None, None)


def inherit(model, database, docs):
    """Fill missing dataset and field descriptions; only fields that are a bare column inherit."""
    for dataset in model.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        table = docs.get((database, *table_of(dataset.get("source"))))
        if not table:
            continue
        if not dataset.get("description") and table["comment"]:
            dataset["description"] = table["comment"]
        for field in dataset.get("fields") or []:
            doc = table["columns"].get(field.get("name")) if isinstance(field, dict) else None
            if doc and sql_of(field) == field.get("name") and not field.get("description"):
                field["description"] = doc
    return model


def document(model):
    """The envelope the platform stores: the whole Ossie document as a JSON string."""
    stored = json.dumps(model)
    if len(stored) > MAX_DOCUMENT:
        raise Refused("the semantic model is larger than 1 MB")
    return {"version": SPEC_VERSION, "semantic_model": stored}


def same(current, model):
    try:
        return json.loads(current["document"]["semantic_model"]) == model
    except (KeyError, TypeError, ValueError):
        return False


def publish(http, url, name, model, headers):
    """'created', 'updated' or 'unchanged'."""
    response = http.get(f"{url}/{quote(name, safe='')}", headers=headers, timeout=30)
    if response.status_code == 404:
        http.post(url, json={"name": name, "document": document(model)}, headers=headers,
                  timeout=30).raise_for_status()
        return "created"
    if response.status_code == 406:
        raise Refused("semantic models are switched off on this platform")
    response.raise_for_status()
    current = response.json()
    if same(current, model):
        return "unchanged"
    http.put(f"{url}/{quote(name, safe='')}", headers=headers, timeout=30,
             json={"document": document(model), "entity-version": current.get("entity-version")}).raise_for_status()
    return "updated"


def main(job, project, token, http=None, docs=None):
    http = http or requests.Session()
    docs = docs or {}
    headers = {"Authorization": f"Bearer {token}"}
    outcome = {"created": [], "updated": [], "unchanged": [], "errors": []}
    for path, database, namespace, name in found(project):
        label = path.relative_to(Path(project) / FOLDER).as_posix()
        try:
            warehouse = job["catalogs"].get(database)
            if not warehouse:
                raise Refused(f"{database or 'the first folder'} is not a database of this environment; "
                              f"use one of {', '.join(sorted(job['catalogs'])) or 'none'}")
            if not namespace:
                raise Refused("put the file in a namespace folder: semantic_models/<database>/<namespace>/<name>.yml")
            if not NAME.match(name):
                raise Refused("a model name uses letters, digits, '-' and '_'")
            try:
                model = inherit(model_of(yaml.safe_load(path.read_text())), database, docs)
            except yaml.YAMLError as exc:
                raise Refused(f"the YAML does not parse ({type(exc).__name__})") from None
            url = (f"{job['catalogUri']}/polaris/v1/{quote(warehouse, safe='')}/namespaces/"
                   f"{quote(chr(31).join(namespace), safe='')}/semantic-models")
            outcome[publish(http, url, name, model, headers)].append(label)
        except Refused as exc:
            outcome["errors"].append({"model": label, "error": str(exc)})
        except requests.HTTPError as exc:
            outcome["errors"].append({"model": label, "error": f"the catalog answered {exc.response.status_code}"})
        except requests.RequestException as exc:
            outcome["errors"].append({"model": label, "error": type(exc).__name__})
    return outcome
