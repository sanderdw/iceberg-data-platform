"""Apache Ossie semantic models as stored by Polaris 1.8, parsed into plain frozen objects.

Polaris keeps a whole Ossie document as a JSON string inside a versioned envelope:
`{"document": {"version", "semantic_model": "<json>"}, "entity-version": n}`. Clients differ in
whether that string holds one model, a list of models or a full document `{version,
semantic_model: [model]}`; all three are accepted. Only the first model of a document is used.
"""

import json
import re
from dataclasses import dataclass, field

from .errors import SemanticError

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Dialects in order of preference: the engine is DuckDB, whose SQL is close to ANSI.
DIALECTS = ("DUCKDB", "ANSI_SQL", "")
# A model can come from another team. Bound what one model may ask of the catalog and the join planner.
MAX_DATASETS = 64
MAX_FIELDS = 512
MAX_RELATIONSHIPS = 256
MAX_METRICS = 256


@dataclass(frozen=True)
class Field:
    name: str
    expression: str
    description: str = ""
    datatype: str = ""
    dimension: bool | None = None


@dataclass(frozen=True)
class Dataset:
    name: str
    namespace: tuple[str, ...]
    table: str
    source: str
    description: str = ""
    primary_key: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)


@dataclass(frozen=True)
class Relationship:
    name: str
    source: str
    target: str
    source_columns: tuple[str, ...]
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class Metric:
    name: str
    expression: str
    description: str = ""
    datatype: str = ""


@dataclass(frozen=True)
class Model:
    name: str
    description: str
    instructions: str
    datasets: tuple[Dataset, ...]
    relationships: tuple[Relationship, ...]
    metrics: tuple[Metric, ...]
    synonyms: dict = field(default_factory=dict, compare=False)

    def dataset(self, name):
        return next((d for d in self.datasets if d.name == name), None)

    def metric(self, name):
        return next((m for m in self.metrics if m.name == name), None)


def text(value, limit=20000):
    return value[:limit] if isinstance(value, str) else ""


def dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def first_model(payload):
    if isinstance(payload, list):
        return next((m for item in payload if (m := first_model(item))), None)
    if not isinstance(payload, dict):
        return None
    if "datasets" in payload or "metrics" in payload:
        return payload
    return first_model(payload.get("semantic_model"))


def expression(item, default=""):
    """The SQL of a field or metric: a plain string, or the best of its `dialects`."""
    found = item.get("expression")
    if isinstance(found, str):
        return found.strip() or default
    options = {text(d.get("dialect")).upper(): text(d.get("expression")).strip()
               for d in dicts(found.get("dialects") if isinstance(found, dict) else None)}
    return next((options[d] for d in DIALECTS if options.get(d)), default)


def instructions(context):
    """Ossie's `ai_context` is an object such as {instructions, synonyms}, or plain text."""
    if isinstance(context, str):
        return context[:8000], {}
    if not isinstance(context, dict):
        return "", {}
    synonyms = context.get("synonyms") if isinstance(context.get("synonyms"), dict) else {}
    return text(context.get("instructions"), 8000), dict(synonyms)


def entity_synonyms(item):
    """Ossie's synonyms of one field or metric: a list of strings in its own `ai_context`."""
    context = item.get("ai_context")
    found = context.get("synonyms") if isinstance(context, dict) else None
    return [text(s, 200) for s in found[:20] if isinstance(s, str)] if isinstance(found, list) else []


def dataset_target(source, namespace):
    """`catalog.namespace….table`, `namespace.table` or `table`, relative to the model's namespace."""
    parts = [p for p in text(source).split(".") if p]
    if not parts:
        return None
    levels = parts[1:-1] if len(parts) > 2 else parts[:-1]
    return tuple(levels) or tuple(namespace), parts[-1]


def names(kind, items):
    seen = set()
    for item in items:
        if not NAME.match(item.name):
            raise SemanticError(422, f"The semantic model has a {kind} with an unsupported name: {item.name[:60]!r}.",
                           "invalid_model")
        if item.name in seen:
            raise SemanticError(422, f"The semantic model names {kind} {item.name!r} twice.", "invalid_model")
        seen.add(item.name)


def limit(kind, items, most):
    if len(items) > most:
        raise SemanticError(422, f"The semantic model has {len(items)} {kind}; Conversational BI reads at most {most}.",
                       "invalid_model")


def dimension(field):
    """Ossie marks a dimension with an object, `{"is_time": bool}`; older documents wrote a boolean.

    None when the model says nothing about the field, so unflagged models keep every field usable.
    """
    value = field.get("dimension")
    if isinstance(value, dict):
        return True
    return value if isinstance(value, bool) else None


def parse(loaded, namespace):
    """A Polaris semantic-model response (or a bare Ossie document) as a `Model`."""
    raw = loaded.get("document", {}).get("semantic_model") if isinstance(loaded, dict) and "document" in loaded \
        else loaded
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    model = first_model(raw)
    if model is None:
        raise SemanticError(422, "The stored semantic model has no datasets.", "invalid_model")
    limit("datasets", dicts(model.get("datasets")), MAX_DATASETS)
    limit("relationships", dicts(model.get("relationships")), MAX_RELATIONSHIPS)
    limit("metrics", dicts(model.get("metrics")), MAX_METRICS)
    datasets = []
    for d in dicts(model.get("datasets")):
        limit(f"fields in dataset {text(d.get('name'))[:60]!r}", dicts(d.get("fields")), MAX_FIELDS)
        target = dataset_target(d.get("source"), namespace)
        if target is None:
            raise SemanticError(422, f"Dataset {text(d.get('name'))[:60]!r} has no source table.", "invalid_model")
        fields = tuple(
            Field(name=text(f.get("name")), expression=expression(f, text(f.get("name"))),
                  description=text(f.get("description"), 2000), datatype=text(f.get("datatype"), 60),
                  dimension=dimension(f))
            for f in dicts(d.get("fields"))
        )
        names("field", fields)
        key = d.get("primary_key")
        datasets.append(Dataset(
            name=text(d.get("name")), namespace=target[0], table=target[1], source=text(d.get("source")),
            description=text(d.get("description"), 2000), fields=fields,
            primary_key=tuple(text(k) for k in key) if isinstance(key, list) else (),
        ))
    relationships = tuple(
        Relationship(name=text(r.get("name")), source=text(r.get("from")), target=text(r.get("to")),
                     source_columns=tuple(text(c) for c in r.get("from_columns") or [] if isinstance(c, str)),
                     target_columns=tuple(text(c) for c in r.get("to_columns") or [] if isinstance(c, str)))
        for r in dicts(model.get("relationships"))
    )
    metrics = tuple(
        Metric(name=text(m.get("name")), expression=expression(m), description=text(m.get("description"), 2000),
               datatype=text(m.get("datatype"), 60))
        for m in dicts(model.get("metrics"))
    )
    names("dataset", datasets)
    names("relationship", relationships)
    names("metric", metrics)
    by_name = {d.name for d in datasets}
    for r in relationships:
        if r.source not in by_name or r.target not in by_name:
            raise SemanticError(422, f"Relationship {r.name!r} joins a dataset the model does not define.", "invalid_model")
        if not r.source_columns or len(r.source_columns) != len(r.target_columns):
            raise SemanticError(422, f"Relationship {r.name!r} needs matching from_columns and to_columns.",
                           "invalid_model")
    text_instructions, synonyms = instructions(model.get("ai_context"))
    # Per-entity synonyms join the model-level map, keyed as agents name them: DATASET.field or metric.
    entities = [(f"{text(d.get('name'))}.{text(f.get('name'))}", f)
                for d in dicts(model.get("datasets")) for f in dicts(d.get("fields"))]
    for key, item in [*entities, *((text(m.get("name")), m) for m in dicts(model.get("metrics")))]:
        if found := entity_synonyms(item):
            synonyms.setdefault(key, found)
    return Model(name=text(model.get("name"), 200), description=text(model.get("description"), 4000),
                 instructions=text_instructions, datasets=tuple(datasets), relationships=relationships,
                 metrics=metrics, synonyms=synonyms)
