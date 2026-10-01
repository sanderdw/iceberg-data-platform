# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.28",
#     "pyiceberg[pyarrow]>=0.12",
#     "duckdb>=1.5",
#     "pytz",
# ]
# ///
"""Profile tables, then draft, check and publish an Apache Ossie semantic model as yourself.

    uv run semantic_model.py profile DATABASE NAMESPACE TABLE [TABLE ...]
    uv run semantic_model.py draft   DATABASE NAMESPACE TABLE [TABLE ...] --name NAME > model.json
    uv run semantic_model.py check   DATABASE NAMESPACE model.json [--questions questions.sql]
    uv run semantic_model.py show    DATABASE NAMESPACE NAME > model.json
    uv run semantic_model.py publish DATABASE NAMESPACE model.json --name NAME [--questions questions.sql]

DATABASE is the catalog name (db-...). NAMESPACE is dotted, for example `analytics` or `analytics.nested`.
It signs in through iceberg_connect.py (run `uv run iceberg_connect.py login` first), so your own Polaris
grants decide what you can read and publish. Tokens are never printed.

A questions file holds one SQL query per business question, each after a `-- Q: <question>` line.
Queries may use the dataset names of the model as tables (FLIGHT, ROUTE, ...).
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import quote

SPEC_VERSION = "0.2.0"
DIALECT = "ANSI_SQL"
NAME = re.compile(r"[A-Za-z0-9_-]+")
# Lines the instructions should cover, as in the flights example; matched case-insensitively.
INSTRUCTION_TOPICS = ("time", "grain", "missing values", "owner", "refresh", "classification")
TIME_TYPES = ("timestamp", "date", "time")
UNGOVERNED = re.compile(r"\(\s*select\b|\b(from|timezone|read_\w+|iceberg_scan)\s*\(", re.IGNORECASE)
DRAFT_INSTRUCTIONS = """\
Time: TODO time zone and what each date or timestamp means.
Grain: TODO one row per ...; keys.
Missing values: TODO what NULL and sentinel values mean.
Owner: TODO team. Refresh: TODO how and how often.
Classification: TODO personal or sensitive data and how to handle it.
State the metric definition, denominator, units, time window and exclusions with every answer."""


# Signing in -------------------------------------------------------------------------------------

def load_connect():
    """Import iceberg_connect.py from ICEBERG_CONNECT, the working directory or the installation."""
    if "iceberg_connect" in sys.modules:
        return sys.modules["iceberg_connect"]
    here = Path(__file__).resolve()
    candidates = [os.environ.get("ICEBERG_CONNECT"), Path.cwd() / "iceberg_connect.py",
                  here.parents[4] / "iceberg_connect.py",  # installation folder
                  here.parents[4] / "user_portal/client/iceberg_connect.py"]  # source repository
    for candidate in filter(None, candidates):
        path = Path(candidate)
        if path.is_file():
            spec = importlib.util.spec_from_file_location("iceberg_connect", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["iceberg_connect"] = module
            spec.loader.exec_module(module)
            return module
    sys.exit("iceberg_connect.py not found. Run from the folder that holds it, or set ICEBERG_CONNECT to its path. "
             "Download it from the user portal: Catalog > Connect from your computer.")


def namespace_parts(text):
    parts = [p for p in text.split(".") if p]
    if not parts:
        raise ValueError("Give a namespace, for example `analytics` or `analytics.nested`.")
    return parts


def quoted(name):
    return '"' + str(name).replace('"', '""') + '"'


def table_reference(namespace, table, alias="lakehouse"):
    # DuckDB shows a nested Iceberg namespace as one dotted schema name.
    return f"{quoted(alias)}.{quoted('.'.join(namespace))}.{quoted(table)}"


# Reading the model ------------------------------------------------------------------------------

def first_model(payload):
    """The model in a document `{version, semantic_model: [model]}` (inner JSON string or list) or a bare model."""
    if isinstance(payload, dict) and isinstance(payload.get("semantic_model"), str):
        payload = json.loads(payload["semantic_model"])
    if isinstance(payload, dict) and isinstance(payload.get("semantic_model"), list):
        payload = payload["semantic_model"][0] if payload["semantic_model"] else None
    if not isinstance(payload, dict):
        raise ValueError("The file holds no semantic model.")  # noqa: TRY004 - bad input, not a programming error
    return payload


def sql_of(item):
    """The ANSI_SQL expression of a field or metric."""
    expression = item.get("expression")
    if isinstance(expression, str):
        return expression
    for dialect in (expression or {}).get("dialects", []):
        if dialect.get("dialect") == DIALECT:
            return dialect.get("expression")
    return None


def source_table(dataset):
    """(namespace parts, table) of `catalog.namespace....table`."""
    parts = [p for p in str(dataset.get("source", "")).split(".") if p]
    if len(parts) < 3:
        raise ValueError(f"Dataset {dataset.get('name')} needs a source like lakehouse.namespace.table.")
    return parts[1:-1], parts[-1]


def check_structure(model, columns=None):
    """Errors that block publishing and warnings that lower the quality, without touching the catalog.

    `columns` maps a dataset name to the set of column names of its table, when known.
    """
    errors, warnings = [], []
    if not model.get("name"):
        errors.append("The model has no name.")
    if not str(model.get("description", "")).strip():
        errors.append("The model has no description; say what it covers and its grain.")
    context = model.get("ai_context")
    instructions = context.get("instructions", "") if isinstance(context, dict) else ""
    if not instructions.strip():
        errors.append("ai_context.instructions is empty; agents need the rules for using this model.")
    for topic in INSTRUCTION_TOPICS:
        if instructions and topic not in instructions.lower():
            warnings.append(f"ai_context.instructions does not mention {topic!r}.")
    datasets = model.get("datasets") or []
    if not datasets:
        errors.append("The model has no datasets.")
    fields = {}
    for dataset in datasets:
        name = dataset.get("name", "")
        if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            errors.append(f"Dataset name {name!r} must be a plain identifier; metrics refer to it as NAME.column.")
            continue
        if name in fields:
            errors.append(f"Dataset {name} appears twice.")
        try:
            source_table(dataset)
        except ValueError as error:
            errors.append(str(error))
        if not str(dataset.get("description", "")).strip():
            warnings.append(f"Dataset {name} has no description.")
        fields[name] = {f.get("name") for f in dataset.get("fields") or []}
        if not fields[name]:
            errors.append(f"Dataset {name} has no fields.")
        for field in dataset.get("fields") or []:
            label = f"{name}.{field.get('name')}"
            if not sql_of(field):
                errors.append(f"Field {label} has no {DIALECT} expression.")
            if not str(field.get("description", "")).strip():
                warnings.append(f"Field {label} has no description.")
            dimension = field.get("dimension")
            if dimension is not None and not (isinstance(dimension, dict) and isinstance(dimension.get("is_time"), bool)):
                errors.append(f"Field {label}: write dimension as {{\"is_time\": true|false}} or leave it out.")
        for key in dataset.get("primary_key") or []:
            if key not in fields[name]:
                errors.append(f"Primary key {name}.{key} is not a field.")
        if columns and name in columns:
            for field in dataset.get("fields") or []:
                if sql_of(field) == field.get("name") and field.get("name") not in columns[name]:
                    errors.append(f"Field {name}.{field.get('name')} is not a column of the table.")
    for relationship in model.get("relationships") or []:
        label = relationship.get("name", "?")
        source, target = relationship.get("from"), relationship.get("to")
        if source not in fields or target not in fields:
            errors.append(f"Relationship {label} joins unknown datasets {source} -> {target}.")
            continue
        sources, targets = relationship.get("from_columns") or [], relationship.get("to_columns") or []
        if not sources or len(sources) != len(targets):
            errors.append(f"Relationship {label} needs from_columns and to_columns of the same length.")
        for column in sources:
            if column not in fields[source]:
                errors.append(f"Relationship {label}: {source}.{column} is not a field.")
        for column in targets:
            if column not in fields[target]:
                errors.append(f"Relationship {label}: {target}.{column} is not a field.")
    metrics = model.get("metrics") or []
    if not metrics:
        warnings.append("The model has no metrics; agents will invent their own definitions.")
    seen = set()
    for metric in metrics:
        label = metric.get("name", "?")
        if label in seen:
            errors.append(f"Metric {label} appears twice.")
        seen.add(label)
        if not sql_of(metric):
            errors.append(f"Metric {label} has no {DIALECT} expression.")
        description = str(metric.get("description", ""))
        if not description.strip():
            errors.append(f"Metric {label} has no description.")
        elif "unit:" not in description.lower():
            warnings.append(f"Metric {label}: end the description with its unit, for example 'Unit: percent.'")
    return errors, warnings


def referenced(expression, names):
    """Dataset names used as `NAME.column`, in order of first use; the first one is the query's base."""
    found = {n: m.start() for n in names if (m := re.search(rf"(?<![\w.]){re.escape(n)}\s*\.", expression))}
    return sorted(found, key=found.get)


def join_path(model, start, targets):
    """JOIN clauses from `start` to every target along the relationships, in either direction."""
    edges = {}
    for r in model.get("relationships") or []:
        pairs = list(zip(r["from_columns"], r["to_columns"], strict=True))
        edges.setdefault(r["from"], []).append((r["to"], [(r["from"], a, r["to"], b) for a, b in pairs]))
        edges.setdefault(r["to"], []).append((r["from"], [(r["to"], b, r["from"], a) for a, b in pairs]))
    joined, clauses = {start}, []
    for target in targets:
        if target in joined:
            continue
        previous, queue = {start: None}, deque([start])
        while queue and target not in previous:
            node = queue.popleft()
            for neighbour, on in edges.get(node, []):
                if neighbour not in previous:
                    previous[neighbour] = (node, on)
                    queue.append(neighbour)
        if target not in previous:
            raise ValueError(f"No relationship path from {start} to {target}.")
        steps, node = [], target
        while previous[node]:
            steps.append((node, previous[node][1]))
            node = previous[node][0]
        for node, on in reversed(steps):
            if node not in joined:
                condition = " AND ".join(f"{quoted(a)}.{quoted(x)} = {quoted(b)}.{quoted(y)}" for a, x, b, y in on)
                clauses.append(f"JOIN {quoted(node)} ON {condition}")
                joined.add(node)
    return clauses


def metric_query(model, metric):
    names = [d["name"] for d in model["datasets"]]
    expression = sql_of(metric)
    used = referenced(expression, names)
    if not used:
        if len(names) != 1:
            raise ValueError("the expression names no dataset; write DATASET.column so agents know what it counts")
        used = names
    return "\n".join([f"SELECT {expression} FROM {quoted(used[0])}", *join_path(model, used[0], used[1:])])


def parse_questions(text):
    """[(question, sql)] from `-- Q: question` headers, each followed by one query."""
    found, current, lines = [], None, []
    for line in text.splitlines():
        header = re.match(r"\s*--\s*Q:\s*(.+)", line)
        if header:
            if current:
                found.append((current, "\n".join(lines).strip().rstrip(";")))
            current, lines = header[1].strip(), []
        elif current:
            lines.append(line)
    if current:
        found.append((current, "\n".join(lines).strip().rstrip(";")))
    return [(q, sql) for q, sql in found if sql]


# Talking to the platform ------------------------------------------------------------------------

def table_columns(ic, database, model):
    """Column names per dataset, read from the catalog with your grants; missing tables become errors."""
    catalog, columns, errors = ic.catalog(database), {}, []
    for dataset in model.get("datasets") or []:
        try:
            namespace, table = source_table(dataset)
            schema = catalog.load_table((*namespace, table)).schema()
            columns[dataset["name"]] = {field.name for field in schema.fields}
        except Exception as error:  # noqa: BLE001 - report every dataset, not only the first failure
            errors.append(f"Dataset {dataset.get('name')}: cannot read its table ({type(error).__name__}).")
    return columns, errors


def bind(connection, model):
    """One temporary view per dataset, named like the dataset, over its Iceberg table."""
    # CAST(timestamptz AS DATE) follows the session time zone; checks must not depend on this computer's.
    connection.execute("SET TimeZone = 'UTC'")
    for dataset in model["datasets"]:
        namespace, table = source_table(dataset)
        connection.execute(f"CREATE OR REPLACE TEMP VIEW {quoted(dataset['name'])} AS "
                           f"SELECT * FROM {table_reference(namespace, table)}")


def run_checks(connection, model, questions):
    """Run every field, metric and question once; check primary keys and relationships. Returns failures."""
    failures = 0

    def run(label, sql, show=False):
        nonlocal failures
        try:
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(10 if show else 1)
            if show:
                header = [c[0] for c in cursor.description]
                print(f"  ok    {label}")
                print("        " + " | ".join(header))
                for row in rows:
                    print("        " + " | ".join("NULL" if v is None else str(v) for v in row))
            else:
                print(f"  ok    {label}: {rows[0][0] if rows and rows[0] else ''}")
            return rows
        except Exception as error:  # noqa: BLE001 - DuckDB raises many error types
            failures += 1
            print(f"  FAIL  {label}: {str(error).splitlines()[0]}")
            return None

    def violations(label, sql):
        nonlocal failures
        try:
            found = connection.execute(sql).fetchone()[0]
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {label}: {str(error).splitlines()[0]}")
            return
        failures += bool(found)
        print(f"  {'FAIL' if found else 'ok  '}  {label}: {found} violations")

    print("Fields")
    for dataset in model["datasets"]:
        for field in dataset["fields"]:
            run(f"{dataset['name']}.{field['name']}", f"SELECT {sql_of(field)} FROM {quoted(dataset['name'])} LIMIT 1")
    print("Keys and relationships")
    for dataset in model["datasets"]:
        keys = ", ".join(quoted(k) for k in dataset.get("primary_key") or [])
        if keys:
            violations(f"{dataset['name']} key ({keys.replace(chr(34), '')}) is unique",
                       f"SELECT count(*) FROM (SELECT {keys} FROM {quoted(dataset['name'])} GROUP BY ALL HAVING count(*) > 1)")
    for r in model.get("relationships") or []:
        pairs = list(zip(r["from_columns"], r["to_columns"], strict=True))
        on = " AND ".join(f"f.{quoted(a)} = t.{quoted(b)}" for a, b in pairs)
        present = " AND ".join(f"f.{quoted(a)} IS NOT NULL" for a, _ in pairs)
        violations(f"{r['name']}: every {r['from']} finds its {r['to']}",
                   f"SELECT count(*) FROM {quoted(r['from'])} f LEFT JOIN {quoted(r['to'])} t ON {on} "
                   f"WHERE {present} AND t.{quoted(pairs[0][1])} IS NULL")
    print("Metrics")
    for metric in model.get("metrics") or []:
        try:
            sql = metric_query(model, metric)
        except ValueError as error:
            failures += 1
            print(f"  FAIL  {metric['name']}: {error}")
            continue
        run(metric["name"], sql)
    if questions:
        print("Questions")
        for question, sql in questions:
            run(question, sql, show=True)
    return failures


MESSAGES = {
    401: "Your sign-in expired. Run: uv run iceberg_connect.py login",
    403: "Your role may not do this. Reading needs the reader role; changing models needs a writer.",
    404: "Unknown database, namespace or model.",
    406: "Semantic models are switched off. An administrator sets POLARIS_SEMANTIC_MODELS=true in .env.",
    409: "The model already exists or someone changed it since you read it. Run show, merge and publish again.",
    400: "Polaris rejected the request. Names use letters, digits, hyphens and underscores.",
}


class Models:
    """The semantic models of one namespace, through the Polaris REST API."""

    def __init__(self, ic, database, namespace):
        import httpx

        class Auth(httpx.Auth):
            def auth_flow(self, request):
                request.headers["Authorization"] = f"Bearer {ic.session().access_token()}"
                yield request

        self.http = httpx.Client(timeout=30, auth=Auth())
        self.base = (f"{ic.CATALOG_URI.rstrip('/')}/polaris/v1/{quote(database, safe='')}"
                     f"/namespaces/{quote(chr(31).join(namespace), safe='')}/semantic-models")

    def send(self, method, name="", body=None, allow=()):
        response = self.http.request(method, self.base + (f"/{quote(name, safe='')}" if name else ""), json=body)
        if response.status_code in allow:
            return response
        if response.is_error:
            # Bodies are not shown: they can echo input.
            raise SystemExit(MESSAGES.get(response.status_code, f"Polaris answered HTTP {response.status_code}."))
        return response

    def load(self, name):
        response = self.send("GET", name, allow=(404,))
        if response.status_code == 404:
            return None
        loaded = response.json()
        return loaded["document"], loaded["entity-version"]

    @staticmethod
    def document(model):
        # Polaris keeps a whole Ossie document, as a JSON string, inside its versioned envelope.
        return {"version": SPEC_VERSION,
                "semantic_model": json.dumps({"version": SPEC_VERSION, "semantic_model": [model]})}

    def publish(self, name, model):
        current = self.load(name)
        if current is None:
            created = self.send("POST", body={"name": name, "document": self.document(model)}, allow=(409,))
            if created.status_code != 409:
                return "created", created.json()["entity-version"]
            current = self.load(name)  # A teammate created it in the meantime.
        body = {"document": self.document(model), "entity-version": current[1]}
        return "updated", self.send("PUT", name, body).json()["entity-version"]


# Commands ---------------------------------------------------------------------------------------

def profile(ic, database, namespace, tables):
    connection = ic.duckdb_connection(database)
    connection.execute("SET TimeZone = 'UTC'")
    for table in tables:
        reference = table_reference(namespace, table)
        rows = connection.execute(f"SELECT count(*) FROM {reference}").fetchone()[0]
        print(f"\n## {'.'.join(namespace)}.{table}: {rows:,} rows")
        summary = connection.execute(f"SUMMARIZE {reference}").fetchall()
        names = [c[0] for c in connection.description]
        for row in summary:
            info = dict(zip(names, row, strict=True))
            print(f"- {info['column_name']} ({info['column_type']}): {info['null_percentage']}% null, "
                  f"~{info['approx_unique']} distinct, min {info['min']!s:.40}, max {info['max']!s:.40}")
            if "VARCHAR" in info["column_type"] and (info["approx_unique"] or 0) <= 25:
                top = connection.execute(
                    f"SELECT {quoted(info['column_name'])}, count(*) FROM {reference} GROUP BY 1 ORDER BY 2 DESC LIMIT 25"
                ).fetchall()
                print("  values: " + ", ".join(f"{'NULL' if v is None else repr(v)} ({n:,})" for v, n in top))


def draft(ic, database, namespace, tables, name):
    catalog = ic.catalog(database)
    datasets = []
    for table in tables:
        schema = catalog.load_table((*namespace, table)).schema()
        fields = []
        for column in schema.fields:
            field = {"name": column.name, "description": column.doc or "TODO",
                     "expression": {"dialects": [{"dialect": DIALECT, "expression": column.name}]}}
            if str(column.field_type).lower().startswith(TIME_TYPES):
                field["dimension"] = {"is_time": True}
            fields.append(field)
        datasets.append({"name": re.sub(r"\W", "_", table).upper(), "source": ".".join(["lakehouse", *namespace, table]),
                         "description": "TODO", "primary_key": [], "fields": fields})
    model = {"name": name, "description": "TODO: what the model covers. One row in <DATASET> per <grain>.",
             "ai_context": {"instructions": DRAFT_INSTRUCTIONS},
             "datasets": datasets, "relationships": [], "metrics": []}
    print(json.dumps({"version": SPEC_VERSION, "semantic_model": [model]}, indent=2))


def governed_warnings(model):
    """Expressions that governed queries (query_semantic_model, Conversational BI) refuse.

    A quick look for the common cases; publishing through the portal reports every one exactly.
    """
    found = []
    for kind, items in [("Metric", model.get("metrics") or []),
                        *[("Field", d.get("fields") or []) for d in model.get("datasets") or []]]:
        for item in items:
            sql = sql_of(item) or ""
            if UNGOVERNED.search(sql):
                found.append(f"{kind} {item.get('name')}: governed queries refuse subqueries and functions such as "
                             "timezone(); keep metrics timeless and window questions with a filter on a time dimension.")
    return found


def check(ic, database, model, questions):
    errors, warnings = check_structure(model)
    if not errors:
        columns, missing = table_columns(ic, database, model)
        errors, warnings = check_structure(model, columns)
        errors += missing
    warnings += governed_warnings(model)
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    if errors:
        return False
    connection = ic.duckdb_connection(database)
    bind(connection, model)
    failures = run_checks(connection, model, questions)
    todo = json.dumps(model).count("TODO")
    if todo:
        print(f"warning: {todo} TODO placeholders left.")
    print(f"\n{len(errors)} errors, {failures} failed checks, {len(warnings)} warnings.")
    return failures == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    def command_with(name, *arguments):
        sub = commands.add_parser(name)
        for argument in ("database", "namespace", *arguments):
            sub.add_argument(argument)
        return sub

    for command in ("profile", "draft"):
        sub = command_with(command)
        sub.add_argument("tables", nargs="+")
        if command == "draft":
            sub.add_argument("--name", required=True, help="model name: letters, digits, - and _")
    command_with("show", "name")
    for command in ("check", "publish"):
        sub = command_with(command)
        sub.add_argument("file", type=Path)
        sub.add_argument("--questions", type=Path, help="SQL file with -- Q: headers")
        if command == "publish":
            sub.add_argument("--name", required=True, help="model name: letters, digits, - and _")
            sub.add_argument("--skip-check", action="store_true", help="publish without running the checks")
    args = parser.parse_args(argv)
    namespace = namespace_parts(args.namespace)
    ic = load_connect()
    try:
        if args.command == "profile":
            profile(ic, args.database, namespace, args.tables)
        elif args.command == "draft":
            draft(ic, args.database, namespace, args.tables, args.name)
        elif args.command == "show":
            found = Models(ic, args.database, namespace).load(args.name)
            if found is None:
                sys.exit(f"No semantic model {args.name!r} in {args.namespace}.")
            print(json.dumps({"version": SPEC_VERSION, "semantic_model": [first_model(found[0])]}, indent=2))
        else:
            model = first_model(json.loads(args.file.read_text()))
            questions = parse_questions(args.questions.read_text()) if args.questions else []
            if args.command == "check":
                sys.exit(0 if check(ic, args.database, model, questions) else 1)
            if not NAME.fullmatch(args.name):
                sys.exit("Model names use letters, digits, hyphens and underscores.")
            if not args.skip_check and not check(ic, args.database, model, questions):
                sys.exit("Not published: fix the errors and failed checks above, or pass --skip-check.")
            action, version = Models(ic, args.database, namespace).publish(args.name, model)
            print(f"{action} {args.namespace}.{args.name}, entity version {version}")
    except ic.LoginRequired as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
