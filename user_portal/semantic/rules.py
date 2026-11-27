"""Checks a semantic model must pass before the portal stores it, and the document Polaris keeps.

The model is an Apache Ossie 0.3.0.dev0 document: one model at the root. `check_structure` covers
the Ossie schema, names and keys plus the platform's own quality rules, without parsing SQL. It
follows the semantic-model agent skill (`.agents/skills/semantic-model/scripts/semantic_model.py`),
which runs on people's own computers without this package; a test keeps both in step.
`references` then applies Ossie's name resolution to every expression. A model arrives from an
agent, so every value is checked for its type first.
"""

import copy
import json
import math
import re

from . import compiler, expressions, ossie, resolve
from .errors import SemanticError
from .ossie import SPEC_VERSION, dicts

MAX_DOCUMENT = 1_000_000
INSTRUCTION_TOPICS = ("time", "grain", "missing values", "owner", "refresh", "classification")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
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


def text(value):
    return value.strip() if isinstance(value, str) else ""


def definition(payload):
    """The Ossie document to store: one model at the root, with this platform's `version`."""
    if not isinstance(payload, dict):
        raise SemanticError(422, "Send one Apache Ossie document: an object with the model's name and datasets.",
                            "invalid_model")
    if "semantic_model" in payload:
        raise SemanticError(422, f"Apache Ossie {SPEC_VERSION} has no semantic_model list: put the model's name, "
                                 "datasets, relationships and metrics at the root of the document.", "invalid_model")
    version = payload.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise SemanticError(422, f"This platform stores Apache Ossie {SPEC_VERSION} documents; this one declares "
                                 f"version {str(version)[:40]!r}.", "invalid_model")
    return {"version": SPEC_VERSION, **{k: v for k, v in payload.items() if k != "version"}}


def document(model):
    """Polaris keeps the whole Ossie document, as a JSON string, inside its versioned envelope."""
    stored = json.dumps({"version": SPEC_VERSION, **{k: v for k, v in model.items() if k != "version"}})
    if len(stored) > MAX_DOCUMENT:
        raise SemanticError(413, "The semantic model is larger than 1 MB.")
    return {"version": SPEC_VERSION, "semantic_model": stored}


def sql_of(item):
    """The expression of a field or metric that the platform runs: see `ossie.expression`."""
    return ossie.expression(item)[0] or None


def source_table(dataset):
    """(namespace parts, table) of a source `namespace....table`, in the model's catalog, as Polaris resolves it."""
    parts = text(dataset.get("source")).split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"Dataset {dataset.get('name')} needs a source like namespace.table.")
    return parts[:-1], parts[-1]


def inherit_descriptions(model, docs):
    """Fill missing dataset and field descriptions from the table comment and column docs.

    `docs` maps a dataset name to `{"comment": str, "columns": {column: doc}}`. Only fields whose
    expression is a bare column inherit, under any field name, so a derived field never borrows a
    column's meaning.
    """
    model = copy.deepcopy(model)
    for dataset in dicts(model.get("datasets")):
        table = docs.get(dataset.get("name"))
        if not table:
            continue
        if not text(dataset.get("description")) and text(table.get("comment")):
            dataset["description"] = table["comment"]
        for field in dicts(dataset.get("fields")):
            doc = table.get("columns", {}).get(sql_of(field) or "")
            if not text(field.get("description")) and text(doc):
                field["description"] = doc
    return model


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


def references(model, columns=None):
    """Ossie's name resolution for every expression: (errors, warnings).

    Errors are references the specification forbids, such as a metric that reads a source column
    as a field. Warnings are valid Ossie that governed queries (query_semantic_model, Conversational
    BI) refuse: SQL outside their allowlist, AGG over a source measure (an Iceberg table has
    none), and metrics that combine fields of several datasets.
    """
    errors, warnings, missing = [], [], {}
    parsed = ossie.parse(model)

    def check(label, build):
        try:
            tree = build()
        except SemanticError as error:
            if error.code == "unsafe_expression":
                warnings.append(f"{label}: governed queries refuse it ({error}). Use only aggregates over fields; "
                                "put time windows in query filters, not subqueries.")
            elif error.code == "no_measures":
                warnings.append(f"{label}: governed queries refuse it. {error}")
            else:
                errors.append(str(error))
            return None
        for column in tree.find_all(expressions.exp.Column):
            if columns and column.table in columns and column.name.upper() not in {c.upper() for c in
                                                                                   columns[column.table]}:
                missing.setdefault((column.table, column.name), label)
        return tree

    for dataset in parsed.datasets:
        for field in dataset.fields:
            check(f"Field {dataset.name}.{field.name}", lambda d=dataset, f=field: resolve.field(d, f))
    for metric in parsed.metrics:
        resolved = check(f"Metric {metric.name}", lambda m=metric: resolve.metric(parsed, m).tree)
        if resolved is None:
            continue
        if not expressions.is_aggregate(resolved):
            warnings.append(f"Metric {metric.name} is not an aggregate, so governed queries refuse it.")
            continue
        try:
            compiler.metric_home(parsed, metric)
        except SemanticError as error:
            warnings.append(f"Metric {metric.name}: governed queries refuse it. {error}")
    errors += [f"{label} reads {column}, which is not a column of {dataset}'s table."
               for (dataset, column), label in missing.items()]
    return errors, warnings


def validate(payload, columns=None):
    """The document to store and its warnings; SemanticError 422 lists everything that blocks it.

    `columns` maps a dataset name to its table's column names, so expressions that read a missing column block it.
    """
    model = definition(payload)
    errors, warnings = check_structure(model, columns)
    if not errors:
        found, cautions = references(model, columns)
        errors, warnings = found, warnings + cautions
    if errors:
        raise SemanticError(422, "The semantic model is not valid: " + " ".join(errors), "invalid_model",
                            {"errors": errors, "warnings": warnings})
    return model, warnings
