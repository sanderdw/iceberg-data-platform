"""Checks a semantic model must pass before the portal stores it, and the document Polaris keeps.

`check_structure` follows the semantic-model agent skill (`.agents/skills/semantic-model/scripts/
semantic_model.py`), which runs on people's own computers without this package; a test keeps both
in step. A model arrives from an agent, so every value is checked for its type first.
"""

import json
import re

from . import expressions
from .errors import SemanticError
from .ossie import dicts

SPEC_VERSION = "0.2.0"
DIALECT = "ANSI_SQL"
MAX_DOCUMENT = 1_000_000
INSTRUCTION_TOPICS = ("time", "grain", "missing values", "owner", "refresh", "classification")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Ossie's logical datatypes. A dimension without is_time is a time dimension when its datatype is temporal;
# only dates and timestamps can be grouped by day, week or month.
DATATYPES = ("String", "Integer", "Decimal", "Float", "Boolean", "Date", "Time", "DateTime", "DateTimeTz", "Opaque")
TIME_DATATYPES = ("Date", "Time", "DateTime", "DateTimeTz")
GRAIN_DATATYPES = ("Date", "DateTime", "DateTimeTz")


def text(value):
    return value.strip() if isinstance(value, str) else ""


def first_model(payload):
    """The model in a whole Ossie document `{version, semantic_model: [model]}`, or a bare model."""
    if isinstance(payload, dict) and isinstance(payload.get("semantic_model"), str):
        try:
            payload = json.loads(payload["semantic_model"])
        except ValueError:
            payload = None
    if isinstance(payload, dict) and isinstance(payload.get("semantic_model"), list):
        payload = payload["semantic_model"][0] if payload["semantic_model"] else None
    if not isinstance(payload, dict):
        raise SemanticError(422, "Send one semantic model, or an Ossie document that holds one.", "invalid_model")
    return payload


def document(model):
    """Polaris keeps a whole Ossie document, as a JSON string, inside its versioned envelope."""
    stored = json.dumps({"version": SPEC_VERSION, "semantic_model": [model]})
    if len(stored) > MAX_DOCUMENT:
        raise SemanticError(413, "The semantic model is larger than 1 MB.")
    return {"version": SPEC_VERSION, "semantic_model": stored}


def sql_of(item):
    """The ANSI_SQL expression of a field or metric."""
    expression = item.get("expression")
    if isinstance(expression, str):
        return expression
    found = expression.get("dialects") if isinstance(expression, dict) else None
    for dialect in dicts(found):
        if dialect.get("dialect") == DIALECT and isinstance(dialect.get("expression"), str):
            return dialect["expression"]
    return None


def source_table(dataset):
    """(namespace parts, table) of `catalog.namespace....table`."""
    parts = [p for p in text(dataset.get("source")).split(".") if p]
    if len(parts) < 3:
        raise ValueError(f"Dataset {dataset.get('name')} needs a source like lakehouse.namespace.table.")
    return parts[1:-1], parts[-1]


def check_structure(model, columns=None):
    """Errors that block publishing and warnings that lower the quality, without touching the catalog.

    `columns` maps a dataset name to the set of column names of its table, when known.
    """
    errors, warnings = [], []
    if not text(model.get("name")):
        errors.append("The model has no name.")
    if not text(model.get("description")):
        errors.append("The model has no description; say what it covers and its grain.")
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
            errors.append(f"Dataset name {name!r} must be a plain identifier; metrics refer to it as NAME.column.")
            continue
        if name in fields:
            errors.append(f"Dataset {name} appears twice.")
        try:
            source_table(dataset)
        except ValueError as error:
            errors.append(str(error))
        if not text(dataset.get("description")):
            warnings.append(f"Dataset {name} has no description.")
        fields[name] = {f.get("name") for f in dicts(dataset.get("fields"))}
        if not fields[name]:
            errors.append(f"Dataset {name} has no fields.")
        for field in dicts(dataset.get("fields")):
            label = f"{name}.{field.get('name')}"
            if not sql_of(field):
                errors.append(f"Field {label} has no {DIALECT} expression.")
            if not text(field.get("description")):
                warnings.append(f"Field {label} has no description.")
            dimension, datatype = field.get("dimension"), field.get("datatype")
            if dimension is not None and not (isinstance(dimension, dict)
                                              and isinstance(dimension.get("is_time", False), bool)):
                errors.append(f"Field {label}: write dimension as {{\"is_time\": true|false}}, {{}} or leave it out.")
            if datatype is not None and datatype not in DATATYPES:
                errors.append(f"Field {label}: datatype {datatype!r} is not an Ossie datatype ({', '.join(DATATYPES)}).")
            elif isinstance(dimension, dict) and dimension.get("is_time", datatype in TIME_DATATYPES) is True \
                    and sql_of(field) != field.get("name") and datatype not in GRAIN_DATATYPES:
                warnings.append(f"Field {label} is a derived time dimension without a date or timestamp datatype, so "
                                "governed queries cannot group it by day, week or month. Add \"datatype\": \"Date\".")
        key = dataset.get("primary_key")
        keys[name] = key if isinstance(key, list) else []
        for column in keys[name]:
            if column not in fields[name]:
                errors.append(f"Primary key {name}.{column} is not a field.")
        if columns and name in columns:
            for field in dicts(dataset.get("fields")):
                if sql_of(field) == field.get("name") and field.get("name") not in columns[name]:
                    errors.append(f"Field {name}.{field.get('name')} is not a column of the table.")
    for relationship in dicts(model.get("relationships")):
        label = relationship.get("name", "?")
        source, target = relationship.get("from"), relationship.get("to")
        if source not in fields or target not in fields:
            errors.append(f"Relationship {label} joins unknown datasets {source} -> {target}.")
            continue
        before = len(errors)
        sources, targets = relationship.get("from_columns") or [], relationship.get("to_columns") or []
        if not isinstance(sources, list) or not isinstance(targets, list) or not sources or len(sources) != len(targets):
            errors.append(f"Relationship {label} needs from_columns and to_columns of the same length.")
            continue
        for column in sources:
            if column not in fields[source]:
                errors.append(f"Relationship {label}: {source}.{column} is not a field.")
        for column in targets:
            if column not in fields[target]:
                errors.append(f"Relationship {label}: {target}.{column} is not a field.")
        if len(errors) == before and set(targets) != set(keys.get(target) or ()):
            warnings.append(f"Relationship {label}: {target}.{'+'.join(map(str, targets))} is not the whole primary key "
                            f"of {target}, so governed queries do not join along it. Point it from the many side to "
                            "the one side's primary key.")
    metrics = dicts(model.get("metrics"))
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
        if metric.get("datatype") is not None and metric.get("datatype") not in DATATYPES:
            errors.append(f"Metric {label}: datatype {metric.get('datatype')!r} is not an Ossie datatype.")
        description = text(metric.get("description"))
        if not description:
            errors.append(f"Metric {label} has no description.")
        elif "unit:" not in description.lower():
            warnings.append(f"Metric {label}: end the description with its unit, for example 'Unit: percent.'")
    return errors, warnings


def query_warnings(model):
    """Metrics that governed queries (query_semantic_model, Conversational BI) will refuse to run."""
    found = []
    for metric in dicts(model.get("metrics")):
        sql = sql_of(metric)
        if not sql:
            continue
        try:
            tree = expressions.parse(sql)
        except SemanticError as error:
            found.append(f"Metric {metric.get('name')}: governed queries refuse it ({error}). "
                         "Use only aggregates over columns; put time windows in query filters, not subqueries.")
            continue
        if not expressions.is_aggregate(tree):
            found.append(f"Metric {metric.get('name')} is not an aggregate, so governed queries refuse it.")
    return found


def validate(model, columns=None):
    """The model to store and its warnings; SemanticError 422 lists everything that blocks it.

    `columns` maps a dataset name to its table's column names, so fields that name a missing column block it.
    """
    model = first_model(model)
    errors, warnings = check_structure(model, columns)
    if errors:
        raise SemanticError(422, "The semantic model is not valid: " + " ".join(errors), "invalid_model",
                            {"errors": errors, "warnings": warnings})
    return model, warnings + query_warnings(model)
