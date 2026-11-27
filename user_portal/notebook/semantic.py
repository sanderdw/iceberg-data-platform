"""Store Apache Ossie semantic models in Polaris with the notebook owner's own token.

Polaris keeps a semantic model beside the tables it describes, in a namespace, and authorizes it
like a table (Polaris privileges SEMANTIC_MODEL_*; a writer role can change models). The API is
beta and must be switched on with ENABLE_SEMANTIC_MODELS. Polaris only stores the document: it
never runs a metric. A model is an Apache Ossie 0.3.0.dev0 document, with the model at its root.
"""

import json
import os
from urllib.parse import quote

import httpx

from user_portal.notebook.credentials import access_token
from user_portal.notebook.duckdb_connection import _catalog, refresh_table_credentials, table_reference

SPEC_VERSION = "0.3.0.dev0"


class NotebookAuth(httpx.Auth):
    """Fetch the notebook owner's current token for every request; Keycloak tokens last 15 minutes.

    Legacy password-mode notebooks have no session endpoint, so their client-credentials token is kept.
    """

    def __init__(self, token):
        self.token = token

    def auth_flow(self, request):
        current = access_token() if os.environ.get("ICEBERG_SESSION_TOKEN_URL") else self.token
        request.headers["Authorization"] = f"Bearer {current}"
        yield request


class SemanticModelError(RuntimeError):
    """A failure that the notebook reader can act on, without response bodies or tokens."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SemanticModelConflict(SemanticModelError):
    """Someone else changed the model after it was loaded."""


def explain(response, name):
    """Turn Polaris's answer into one sentence. Bodies are not shown: they can echo user input."""
    status = response.status_code
    messages = {
        403: "Your role may not do this. Reading needs the reader role; changing models needs a writer.",
        406: "Semantic models are switched off. An administrator sets POLARIS_SEMANTIC_MODELS=true in .env.",
        409: f"Semantic model {name!r} already exists or was changed by someone else.",
        400: f"Polaris rejected {name!r}. Names use letters, digits, hyphens and underscores.",
        401: "Your session expired. Reload the notebook from the portal.",
    }
    return messages.get(status, f"Polaris could not handle semantic model {name!r} (HTTP {status}).")


class SemanticModels:
    """The semantic models of one namespace."""

    def __init__(self, client, base):
        self.client, self.base = client, base

    @classmethod
    def connect(cls, namespace):
        with httpx.Client(timeout=30) as discovery:
            token, endpoint, warehouse, *_ = _catalog(discovery)
        client = httpx.Client(timeout=30, auth=NotebookAuth(token))
        base = (
            f"{endpoint}/polaris/v1/{quote(warehouse, safe='')}"
            f"/namespaces/{quote(chr(31).join(namespace), safe='')}/semantic-models"
        )
        return cls(client, base)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, method, name="", body=None, *, params=None, allow=()):
        url = self.base + (f"/{quote(name, safe='')}" if name else "")
        response = self.client.request(method, url, json=body, params=params)
        if response.status_code in allow:
            return response
        if response.is_error:
            error = SemanticModelConflict if response.status_code == 409 and method == "PUT" else SemanticModelError
            raise error(explain(response, name), response.status_code)
        return response

    def names(self):
        found, seen, token = [], set(), None
        while True:
            page = self.send("GET", params={"pageToken": token} if token else None).json()
            found += [i["name"] for i in page["identifiers"]]
            token = page.get("next-page-token")
            if not token:
                return sorted(found)
            if token in seen:
                raise SemanticModelError("Polaris repeated a page of semantic models. Try again later.")
            seen.add(token)

    def load(self, name):
        """The stored model and its entity version, or None when it does not exist."""
        response = self.send("GET", name, allow=(404,))
        if response.status_code == 404:
            return None
        loaded = response.json()
        stored = json.loads(loaded["document"]["semantic_model"])
        return (stored if isinstance(stored, dict) else None), loaded["entity-version"]

    def document(self, model):
        # Polaris keeps the whole Ossie document, as a JSON string, inside its versioned envelope.
        ossie = {"version": SPEC_VERSION, **{k: v for k, v in model.items() if k != "version"}}
        return {"version": SPEC_VERSION, "semantic_model": json.dumps(ossie)}

    def create(self, name, model):
        return self.send("POST", body={"name": name, "document": self.document(model)}).json()["entity-version"]

    def update(self, name, model, entity_version):
        """Replace the model only if nobody changed it since `entity_version` was read."""
        body = {"document": self.document(model), "entity-version": entity_version}
        return self.send("PUT", name, body).json()["entity-version"]

    def publish(self, name, model):
        """Create the model or replace the current one; returns what happened and the new version."""
        current = self.load(name)
        if current is None:
            try:
                return "created", self.create(name, model)
            except SemanticModelError as error:
                # A teammate created it in the meantime: replace theirs, like a rerun would.
                if error.status != 409:
                    raise
                current = self.load(name)
        return "updated", self.update(name, model, current[1])

    def drop(self, name):
        self.send("DELETE", name, allow=(404,))


# Reading a stored model. Everything below takes names and SQL from the model itself, so a reader
# needs no local copy. Identifiers are quoted; metric expressions are SQL by design and run in the
# reader's own read-only DuckDB session with the reader's own Polaris permissions.


def quoted(name):
    return '"' + str(name).replace('"', '""') + '"'


def dataset_table(dataset):
    """The Iceberg namespace and table of a dataset's source, `namespace….table` in the attached catalog."""
    parts = dataset["source"].split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"Dataset {dataset['name']} needs a source like namespace.table.")
    return parts[:-1], parts[-1]


def expression_sql(item, dialect=None):
    """A field's or metric's SQL: the portable `sql` (OSSIE_SQL_2026), or the expression in `dialect`."""
    expression = item["expression"]
    if dialect is None and expression.get("sql"):
        return expression["sql"]
    wanted = dialect or "OSSIE_SQL_2026"
    found = next((d["expression"] for d in expression.get("dialects", []) if d["dialect"] == wanted), None)
    if found is None:
        raise KeyError(f"{item['name']!r} has no {wanted} expression.")
    return found


def fields_sql(dataset, source):
    """SELECT one column per field over `source`: the dataset as keys, relationships and metrics see it."""
    columns = ", ".join(f"{expression_sql(f)} AS {quoted(f['name'])}" for f in dataset.get("fields", []))
    return f"SELECT {columns} FROM {source}"


def bind_datasets(connection, model):
    """One temporary view per dataset, named like the dataset, with its fields over its Iceberg table."""
    for dataset in model["datasets"]:
        namespace, table = dataset_table(dataset)
        refresh_table_credentials(connection, namespace, table, secret_name=f"model_{dataset['name']}")
        connection.execute(
            f"CREATE OR REPLACE TEMP VIEW {quoted(dataset['name'])} AS "
            f"{fields_sql(dataset, table_reference(namespace, table))}"
        )


def metric_sql(model, name, dialect=None):
    """A model metric's SQL, which reads fields as DATASET.field: it runs over the views of bind_datasets."""
    metric = next((m for m in model.get("metrics", []) if m["name"] == name), None)
    if metric is None:
        raise KeyError(f"The semantic model has no metric {name!r}.")
    return expression_sql(metric, dialect)


def joins(model, *names):
    """JOIN clauses that follow the named relationships, in order: `from` must already be in the query."""
    relationships = {r["name"]: r for r in model["relationships"]}
    clauses = []
    for name in names:
        r = relationships[name]
        on = " AND ".join(
            f"{quoted(r['from'])}.{quoted(a)} = {quoted(r['to'])}.{quoted(b)}"
            for a, b in zip(r["from_columns"], r["to_columns"], strict=True)
        )
        clauses.append(f"JOIN {quoted(r['to'])} ON {on}")
    return "\n".join(clauses)


def model_check_queries(model):
    """Checks from the stored model, as (label, SQL counting violations): primary keys are unique and
    every relationship finds its target. Plain SQL over one relation per dataset, for any engine."""
    queries = []
    for d in model["datasets"]:
        keys = ", ".join(quoted(k) for k in d.get("primary_key", []))
        if keys:
            sql = f"SELECT count(*) FROM (SELECT {keys} FROM {quoted(d['name'])} GROUP BY ALL HAVING count(*) > 1)"
            queries.append((f"{d['name']} key ({keys.replace(chr(34), '')}) is unique", sql))
    for r in model["relationships"]:
        pairs = list(zip(r["from_columns"], r["to_columns"], strict=True))
        on = " AND ".join(f"f.{quoted(a)} = t.{quoted(b)}" for a, b in pairs)
        present = " AND ".join(f"f.{quoted(a)} IS NOT NULL" for a, _ in pairs)
        sql = (
            f"SELECT count(*) FROM {quoted(r['from'])} f LEFT JOIN {quoted(r['to'])} t ON {on} "
            f"WHERE {present} AND t.{quoted(pairs[0][1])} IS NULL"
        )
        queries.append((f"{r['name']}: every {r['from']} finds its {r['to']}", sql))
    return queries


def model_checks(connection, model):
    """Evidence from the stored model, run in DuckDB: primary keys are unique and every relationship finds its target."""
    return [{"check": label, "violations": connection.execute(sql).fetchall()[0][0]}
            for label, sql in model_check_queries(model)]


def scan_datasets(catalog, model):
    """One lazy Polars frame per dataset, keyed by the dataset name, with its fields over its table.

    `catalog` is a PyIceberg catalog signed in as the reader, such as `connection.connect()`.
    """
    import polars as pl

    from user_portal.notebook.connection import polars_storage_options

    scans = {}
    for dataset in model["datasets"]:
        namespace, name = dataset_table(dataset)
        table = catalog.load_table((*namespace, name))
        scan = pl.scan_iceberg(table, storage_options=polars_storage_options(table))
        scans[dataset["name"]] = scan.select(
            [pl.sql_expr(expression_sql(f)).alias(f["name"]) for f in dataset.get("fields", [])])
    return scans
