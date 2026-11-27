# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.28",
#     "pyiceberg[pyarrow]>=0.12",
#     "duckdb>=2.0",
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
import math
import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import quote

SPEC_VERSION = "0.3.0.dev0"
# Expressions in order of preference, as the platform picks them: it runs DuckDB.
DIALECTS = ("DUCKDB", "OSSIE_SQL_2026", "ANSI_SQL")
NAME = re.compile(r"[A-Za-z0-9_-]+")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Lines the instructions should cover, as in the flights example; matched case-insensitively.
INSTRUCTION_TOPICS = ("time", "grain", "missing values", "owner", "refresh", "classification")
TIME_TYPES = ("timestamp", "date", "time")
# Ossie's logical datatypes. A dimension without is_time is a time dimension when its datatype is temporal;
# only dates and timestamps can be grouped by day, week or month.
DATATYPES = ("String", "Integer", "Decimal", "Float", "Boolean", "Date", "Time", "DateTime", "DateTimeTz", "Opaque")
TIME_DATATYPES = ("Date", "Time", "DateTime", "DateTimeTz")
GRAIN_DATATYPES = ("Date", "DateTime", "DateTimeTz")
# The keys the Ossie schema allows on each object; it allows no others.
KEYS = {
    "model": ("version", "name", "description", "ai_context", "datasets", "relationships", "metrics",
              "nonportable_extensions"),
    "dataset": ("name", "source", "primary_key", "unique_keys", "description", "ai_context", "fields", "metrics",
                "nonportable_extensions"),
    "field": ("name", "expression", "dimension", "label", "description", "datatype", "ai_context",
              "nonportable_extensions"),
    "metric": ("name", "expression", "description", "datatype", "ai_context", "nonportable_extensions"),
    "relationship": ("name", "from", "to", "from_columns", "to_columns", "ai_context", "nonportable_extensions"),
}
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

def read_model(payload):
    """The Apache Ossie 0.3.0.dev0 document in a file: one model at the root, with its version."""
    if not isinstance(payload, dict):
        raise ValueError("The file holds no semantic model.")  # noqa: TRY004 - bad input, not a programming error
    if "semantic_model" in payload:
        raise ValueError(f"Apache Ossie {SPEC_VERSION} has no semantic_model list: put the model's name, datasets, "
                         "relationships and metrics at the root of the document.")
    return {"version": SPEC_VERSION, **{k: v for k, v in payload.items() if k != "version"}}


def text(value):
    return value.strip() if isinstance(value, str) else ""


def dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def sql_of(item):
    """The expression the platform runs: a DUCKDB dialect, else the portable sql, else OSSIE_SQL_2026 or ANSI_SQL."""
    expression = item.get("expression")
    if not isinstance(expression, dict):
        return None
    options = {}
    for dialect in dicts(expression.get("dialects")):
        options.setdefault(text(dialect.get("dialect")).upper(), text(dialect.get("expression")))
    if text(expression.get("sql")):
        options["OSSIE_SQL_2026"] = text(expression["sql"])
    return next((options[d] for d in DIALECTS if options.get(d)), None)


def source_table(dataset):
    """(namespace parts, table) of a source `namespace....table`, in the model's catalog, as Polaris resolves it."""
    parts = text(dataset.get("source")).split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"Dataset {dataset.get('name')} needs a source like namespace.table.")
    return parts[:-1], parts[-1]


# The same checks as the user portal's user_portal/semantic/rules.py; a test keeps both in step.

def unknown_keys(item, kind, label):
    return [f"{label}: Ossie has no key {k!r} here." for k in item if k not in KEYS[kind]]


def expression_errors(item, label):
    """Ossie's Expression object: a non-empty `sql`, or `dialects` of {dialect, expression}, or both."""
    found = item.get("expression")
    if not isinstance(found, dict):
        return [f"{label} needs an expression: {{\"sql\": \"...\"}}."]
    errors = [f"{label}: the expression has no key {k!r}; use sql and dialects." for k in found
              if k not in ("sql", "dialects")]
    dialects = found.get("dialects")
    if "sql" in found and not text(found.get("sql")):
        errors.append(f"{label}: expression.sql is empty.")
    if dialects is not None and (not isinstance(dialects, list) or not dialects or not all(
            isinstance(d, dict) and set(d) == {"dialect", "expression"} and text(d.get("dialect"))
            and text(d.get("expression")) for d in dialects)):
        errors.append(f"{label}: write expression.dialects as a list of {{\"dialect\", \"expression\"}}.")
    if not text(found.get("sql")) and dialects is None:
        errors.append(f"{label} needs an expression: {{\"sql\": \"...\"}}.")
    return errors


def json_value(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and json_value(v) for k, v in value.items())
    if isinstance(value, list):
        return all(json_value(v) for v in value)
    return value is None or isinstance(value, str | int | bool)


def extension_errors(item, label):
    """Non-portable extensions: [{vendor_name, data}], where data is a JSON object Ossie carries as is."""
    found = item.get("nonportable_extensions")
    if found is None:
        return []
    if not isinstance(found, list) or not all(
            isinstance(e, dict) and set(e) == {"vendor_name", "data"} and text(e.get("vendor_name"))
            and isinstance(e.get("data"), dict) and json_value(e["data"]) for e in found):
        return [(f"{label}: write nonportable_extensions as a list of {{\"vendor_name\", \"data\"}}, where data "
                 "is a JSON object.")]
    return []


def context_errors(item, label):
    """`ai_context` is text, or an object whose instructions are text and synonyms and examples lists of text."""
    found = item.get("ai_context")
    if found is None or isinstance(found, str):
        return []
    if not isinstance(found, dict):
        return [f"{label}: ai_context is text or an object."]
    errors = [f"{label}: ai_context.instructions is text."] if not isinstance(found.get("instructions", ""), str) \
        else []
    return errors + [f"{label}: ai_context.{k} is a list of text." for k in ("synonyms", "examples")
                     if k in found and not (isinstance(found[k], list) and all(isinstance(s, str) for s in found[k]))]


def key_errors(key, fields, label):
    """A key is a non-empty list of field names, each named once (names compare without regard to case)."""
    if not isinstance(key, list) or not key or not all(isinstance(k, str) for k in key):
        return [f"{label} is a non-empty list of field names."]
    errors = [f"{label}: {k} is not a field." for k in key if k.upper() not in fields]
    if len({k.upper() for k in key}) != len(key):
        errors.append(f"{label} names a field twice.")
    return errors


def check_structure(model, columns=None):
    """Errors that block publishing and warnings that lower the quality, without touching the catalog.

    `columns` maps a dataset name to the set of column names of its table, when known.
    """
    errors, warnings = [], []
    errors += unknown_keys(model, "model", "The model")
    if model.get("version", SPEC_VERSION) != SPEC_VERSION:
        errors.append(f"The model declares version {model.get('version')!r}; write {SPEC_VERSION!r}.")
    if not text(model.get("name")):
        errors.append("The model has no name.")
    if not text(model.get("description")):
        errors.append("The model has no description; say what it covers and its grain.")
    errors += context_errors(model, "The model") + extension_errors(model, "The model")
    context = model.get("ai_context")
    instructions = text(context.get("instructions")) if isinstance(context, dict) else ""
    if not instructions:
        errors.append("ai_context.instructions is empty; agents need the rules for using this model.")
    for topic in INSTRUCTION_TOPICS:
        if instructions and topic not in instructions.lower():
            warnings.append(f"ai_context.instructions does not mention {topic!r}.")
    datasets = dicts(model.get("datasets"))
    if not datasets:
        errors.append("The model has no datasets.")
    fields, keys = {}, {}
    for dataset in datasets:
        name = dataset.get("name")
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            errors.append(f"Dataset name {name!r} must be a plain identifier; metrics refer to it as NAME.field.")
            continue
        if name.upper() in fields:
            errors.append(f"Dataset {name} appears twice.")
        errors += unknown_keys(dataset, "dataset", f"Dataset {name}")
        errors += context_errors(dataset, f"Dataset {name}") + extension_errors(dataset, f"Dataset {name}")
        try:
            source_table(dataset)
        except ValueError as error:
            errors.append(str(error))
        if not text(dataset.get("description")):
            warnings.append(f"Dataset {name} has no description.")
        members = [*dicts(dataset.get("fields")), *dicts(dataset.get("metrics"))]
        fields[name.upper()] = {text(f.get("name")).upper() for f in dicts(dataset.get("fields"))}
        if not fields[name.upper()]:
            errors.append(f"Dataset {name} has no fields.")
        seen = set()
        for member in members:
            member_name = member.get("name")
            if not isinstance(member_name, str) or not IDENTIFIER.fullmatch(member_name):
                errors.append(f"Dataset {name}: {member_name!r} must be a plain identifier.")
            elif member_name.upper() in seen:
                errors.append(f"Dataset {name} names {member_name} twice; its fields and metrics share one namespace.")
            else:
                seen.add(member_name.upper())
        for field in dicts(dataset.get("fields")):
            label = f"Field {name}.{field.get('name')}"
            errors += unknown_keys(field, "field", label) + expression_errors(field, label)
            errors += context_errors(field, label) + extension_errors(field, label)
            if not text(field.get("description")):
                warnings.append(f"{label} has no description.")
            dimension, datatype = field.get("dimension"), field.get("datatype")
            if dimension is not None and not (isinstance(dimension, dict) and set(dimension) <= {"is_time"}
                                              and isinstance(dimension.get("is_time", False), bool)):
                errors.append(f"{label}: write dimension as {{\"is_time\": true|false}}, {{}} or leave it out.")
            if datatype is not None and datatype not in DATATYPES:
                errors.append(f"{label}: datatype {datatype!r} is not an Ossie datatype ({', '.join(DATATYPES)}).")
            elif isinstance(dimension, dict) and dimension.get("is_time", datatype in TIME_DATATYPES) is True \
                    and sql_of(field) != field.get("name") and datatype not in GRAIN_DATATYPES:
                warnings.append(f"{label} is a derived time dimension without a date or timestamp datatype, so "
                                "governed queries cannot group it by day, week or month. Add \"datatype\": \"Date\".")
        if "primary_key" in dataset:
            errors += key_errors(dataset["primary_key"], fields[name.upper()], f"Primary key of {name}")
        unique = dataset.get("unique_keys")
        if unique is not None and (not isinstance(unique, list) or not unique):
            errors.append(f"Unique keys of {name} are a non-empty list of keys.")
            unique = []
        found = [k for k in [dataset.get("primary_key"), *(unique or [])] if isinstance(k, list) and k]
        keys[name.upper()] = [{str(k).upper() for k in key} for key in found]
        declared = []
        for key in unique or []:
            errors += key_errors(key, fields[name.upper()], f"A unique key of {name}")
            if isinstance(key, list) and {str(k).upper() for k in key} in declared:
                warnings.append(f"Unique keys of {name}: {', '.join(map(str, key))} is declared twice.")
            declared.append({str(k).upper() for k in key} if isinstance(key, list) else set())
        if columns and name in columns:
            available = {c.upper() for c in columns[name]}
            for field in dicts(dataset.get("fields")):
                if sql_of(field) == field.get("name") and str(field.get("name")).upper() not in available:
                    errors.append(f"Field {name}.{field.get('name')} is not a column of the table.")
    names = set()
    for relationship in dicts(model.get("relationships")):
        label = relationship.get("name", "?")
        errors += unknown_keys(relationship, "relationship", f"Relationship {label}")
        errors += context_errors(relationship, f"Relationship {label}")
        errors += extension_errors(relationship, f"Relationship {label}")
        if not text(label):
            errors.append("A relationship has no name.")
        elif label.upper() in names:
            errors.append(f"Relationship {label} appears twice.")
        names.add(str(label).upper())
        source, target = str(relationship.get("from")), str(relationship.get("to"))
        if source.upper() not in fields or target.upper() not in fields:
            errors.append(f"Relationship {label} joins unknown datasets {source} -> {target}.")
            continue
        before = len(errors)
        sources, targets = relationship.get("from_columns") or [], relationship.get("to_columns") or []
        if not isinstance(sources, list) or not isinstance(targets, list) or not sources or len(sources) != len(targets):
            errors.append(f"Relationship {label} needs from_columns and to_columns of the same length.")
            continue
        for column in sources:
            if str(column).upper() not in fields[source.upper()]:
                errors.append(f"Relationship {label}: {source}.{column} is not a field.")
        for column in targets:
            if str(column).upper() not in fields[target.upper()]:
                errors.append(f"Relationship {label}: {target}.{column} is not a field.")
        if len(errors) == before and {str(c).upper() for c in targets} not in keys.get(target.upper(), []):
            warnings.append(f"Relationship {label}: {target}.{'+'.join(map(str, targets))} is not a whole primary or "
                            f"unique key of {target}, so governed queries do not join along it. Point it from the "
                            "many side to the one side's key.")
    scoped = [(f"{d.get('name')}.", m) for d in datasets for m in dicts(d.get("metrics"))]
    metrics = [("", m) for m in dicts(model.get("metrics"))] + scoped
    if not metrics:
        warnings.append("The model has no metrics; agents will invent their own definitions.")
    seen = set()
    for prefix, metric in metrics:
        label = f"{prefix}{metric.get('name', '?')}"
        if not prefix:
            if str(metric.get("name")).upper() in seen:
                errors.append(f"Metric {label} appears twice.")
            seen.add(str(metric.get("name")).upper())
        errors += unknown_keys(metric, "metric", f"Metric {label}") + expression_errors(metric, f"Metric {label}")
        errors += context_errors(metric, f"Metric {label}") + extension_errors(metric, f"Metric {label}")
        if metric.get("datatype") is not None and metric.get("datatype") not in DATATYPES:
            errors.append(f"Metric {label}: datatype {metric.get('datatype')!r} is not an Ossie datatype.")
        description = text(metric.get("description"))
        if not description:
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


AGG = re.compile(r"\bAGG\s*\(\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\)", re.IGNORECASE)


def relation(dataset):
    """A dataset as its metrics read it: its fields, and its table's row as `source` for source.column."""
    namespace, table = source_table(dataset)
    columns = [f"{sql_of(f)} AS {quoted(f['name'])}" for f in dicts(dataset.get("fields"))]
    if not any(str(f.get("name")).upper() == "SOURCE" for f in dicts(dataset.get("fields"))):
        columns.append('"t" AS "source"')
    return f"(SELECT {', '.join(columns)} FROM {table_reference(namespace, table)} AS \"t\")"


def dataset_of(model, name):
    return next((d for d in dicts(model.get("datasets")) if str(d.get("name")).upper() == str(name).upper()), None)


def metric_query(model, metric, dataset=None):
    """SQL that evaluates one metric over the whole of its datasets.

    A dataset's own metric reads its fields by name and its table's columns as source.column. A
    model's metric reads DATASET.field and joins from the first dataset it names; AGG(DATASET.metric)
    becomes that dataset's metric.
    """
    expression = sql_of(metric)
    if dataset is not None:
        return f"SELECT {expression} FROM {relation(dataset)} AS {quoted(dataset['name'])}"
    names = [d["name"] for d in model["datasets"]]

    def inline(match):
        found = dataset_of(model, match[1])
        inner = next((m for m in dicts(found.get("metrics")) if str(m.get("name")).upper() == match[2].upper()),
                     None) if found else None
        if inner is None:
            raise ValueError(f"AGG({match[1]}.{match[2]}) names no metric of a dataset")
        return f"({sql_of(inner)})"

    used = referenced(expression, names)
    expression = AGG.sub(inline, expression)
    if not used:
        if len(names) != 1:
            raise ValueError("the expression names no dataset; write DATASET.field so agents know what it counts")
        used = names
    if len(used) > 1 and AGG.search(sql_of(metric)):
        raise ValueError("AGG(DATASET.metric) combined with other datasets is checked when you publish")
    relations = {d["name"]: relation(d) for d in model["datasets"]}
    clauses = join_path(model, used[0], used[1:])
    for name in names:
        clauses = [c.replace(f"JOIN {quoted(name)} ON", f"JOIN {relations[name]} AS {quoted(name)} ON") for c in clauses]
    return "\n".join([f"SELECT {expression} FROM {relations[used[0]]} AS {quoted(used[0])}", *clauses])


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
    """One temporary view per dataset, named like the dataset, with its fields over its Iceberg table.

    Keys, relationships and questions read fields, as Ossie names them; a field that fails is left out.
    """
    # CAST(timestamptz AS DATE) follows the session time zone; checks must not depend on this computer's.
    connection.execute("SET TimeZone = 'UTC'")
    for dataset in model["datasets"]:
        namespace, table = source_table(dataset)
        reference = table_reference(namespace, table)
        working = []
        for field in dataset["fields"]:
            try:
                connection.execute(f"SELECT {sql_of(field)} FROM {reference} LIMIT 0")
                working.append(f"{sql_of(field)} AS {quoted(field['name'])}")
            except Exception:  # noqa: BLE001, S112 - run_checks reports every failing field
                continue
        connection.execute(f"CREATE OR REPLACE TEMP VIEW {quoted(dataset['name'])} AS "
                           f"SELECT {', '.join(working) or 'NULL AS no_fields'} FROM {reference}")


def run_checks(connection, model, questions):
    """Run every field, metric and question once; check primary keys and relationships. Returns failures."""
    failures = 0

    def run(label, sql, show=False):
        nonlocal failures
        try:
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(10 if show else 1)
            # Results stream: run the query to its end, so an error in a later row still counts.
            connection.complete()
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
            found = connection.execute(sql).fetchall()[0][0]
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {label}: {str(error).splitlines()[0]}")
            return
        failures += bool(found)
        print(f"  {'FAIL' if found else 'ok  '}  {label}: {found} violations")

    print("Fields")
    for dataset in model["datasets"]:
        namespace, table = source_table(dataset)
        for field in dataset["fields"]:
            run(f"{dataset['name']}.{field['name']}",
                f"SELECT {sql_of(field)} FROM {table_reference(namespace, table)} LIMIT 1")
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
    scoped = [(f"{d['name']}.{m['name']}", m, d) for d in model["datasets"] for m in dicts(d.get("metrics"))]
    for label, metric, dataset in [(m["name"], m, None) for m in model.get("metrics") or []] + scoped:
        try:
            sql = metric_query(model, metric, dataset)
        except ValueError as error:
            failures += 1
            print(f"  FAIL  {label}: {error}")
            continue
        run(label, sql)
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
        return json.loads(loaded["document"]["semantic_model"]), loaded["entity-version"]

    @staticmethod
    def document(model):
        # Polaris keeps the whole Ossie document, as a JSON string, inside its versioned envelope.
        return {"version": SPEC_VERSION, "semantic_model": json.dumps(read_model(model))}

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
        rows = connection.execute(f"SELECT count(*) FROM {reference}").fetchall()[0][0]
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


def ossie_datatype(iceberg_type):
    """The Ossie datatype of an Iceberg column type, or None for types Ossie has no word for."""
    kind = iceberg_type.lower().split("(")[0]
    if kind.startswith("timestamptz"):
        return "DateTimeTz"
    if kind.startswith("timestamp"):
        return "DateTime"
    return {"string": "String", "uuid": "String", "int": "Integer", "long": "Integer", "float": "Float",
            "double": "Float", "decimal": "Decimal", "boolean": "Boolean", "date": "Date", "time": "Time"}.get(kind)


def draft(ic, database, namespace, tables, name):
    catalog = ic.catalog(database)
    datasets = []
    for table in tables:
        schema = catalog.load_table((*namespace, table)).schema()
        fields = []
        for column in schema.fields:
            field = {"name": column.name, "description": column.doc or "TODO", "expression": {"sql": column.name}}
            if datatype := ossie_datatype(str(column.field_type)):
                field["datatype"] = datatype
            if str(column.field_type).lower().startswith(TIME_TYPES):
                field["dimension"] = {"is_time": True}
            fields.append(field)
        datasets.append({"name": re.sub(r"\W", "_", table).upper(), "source": ".".join([*namespace, table]),
                         "description": "TODO", "primary_key": [], "fields": fields})
    model = {"version": SPEC_VERSION, "name": name,
             "description": "TODO: what the model covers. One row in <DATASET> per <grain>.",
             "ai_context": {"instructions": DRAFT_INSTRUCTIONS},
             "datasets": datasets, "relationships": [], "metrics": []}
    print(json.dumps(model, indent=2))


def governed_warnings(model):
    """Expressions that governed queries (query_semantic_model, Conversational BI) refuse.

    A quick look for the common cases; publishing through the portal reports every one exactly.
    """
    found = []
    for kind, items in [("Metric", model.get("metrics") or []),
                        *[("Metric", d.get("metrics") or []) for d in model.get("datasets") or []],
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
            print(json.dumps(found[0], indent=2))
        else:
            model = read_model(json.loads(args.file.read_text()))
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
