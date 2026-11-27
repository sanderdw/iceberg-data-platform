"""Apache Ossie 0.3.0.dev0 semantic models as stored by Polaris, parsed into plain frozen objects.

Polaris keeps the Ossie document as a JSON string inside a versioned envelope:
`{"document": {"version": "0.3.0.dev0", "semantic_model": "<json>"}, "entity-version": n}`. The
JSON is one Ossie document with the model at its root: `{version, name, datasets, relationships,
metrics, ...}`. Metrics live on the model (model-scoped, addressed by their name) and on datasets
(dataset-scoped, addressed as `DATASET.metric`).

Names are regular SQL identifiers, so they compare without regard to case, as Ossie specifies.
"""

import json
import re
from dataclasses import dataclass, field

from ..errors import CbiError

SPEC_VERSION = "0.3.0.dev0"
PORTABLE = "OSSIE_SQL_2026"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Dialects in order of preference: the engine is DuckDB; Ossie's portable SQL is the top-level `sql`.
DIALECTS = ("DUCKDB", PORTABLE, "ANSI_SQL")
# A model can come from another team. Bound what one model may ask of the catalog and the join planner.
MAX_DATASETS = 64
MAX_FIELDS = 512
MAX_RELATIONSHIPS = 256
MAX_METRICS = 256
MAX_EXAMPLES = 20


def key(name):
    """The normalized form of a declared name: a regular identifier is upper-cased."""
    return name.upper()


def find(items, name):
    return next((i for i in items if key(i.name) == key(name)), None)


@dataclass(frozen=True)
class Field:
    name: str
    expression: str
    dialect: str = PORTABLE
    description: str = ""
    datatype: str = ""
    dimension: bool | None = None


@dataclass(frozen=True)
class Metric:
    # How callers address it: `name` for a model-scoped metric, `DATASET.name` for a dataset-scoped one.
    name: str
    expression: str
    dialect: str = PORTABLE
    description: str = ""
    datatype: str = ""
    dataset: str | None = None

    @property
    def local(self):
        return self.name.rpartition(".")[2]


@dataclass(frozen=True)
class Dataset:
    name: str
    namespace: tuple[str, ...]
    table: str
    source: str
    description: str = ""
    primary_key: tuple[str, ...] = ()
    unique_keys: tuple[tuple[str, ...], ...] = ()
    fields: tuple[Field, ...] = ()
    metrics: tuple[Metric, ...] = ()

    def field(self, name):
        return find(self.fields, name)

    def metric(self, name):
        return next((m for m in self.metrics if key(m.local) == key(name)), None)

    @property
    def keys(self):
        """Every declared key, primary first, as sets of normalized field names."""
        return [{key(k) for k in found} for found in (self.primary_key, *self.unique_keys) if found]


@dataclass(frozen=True)
class Relationship:
    name: str
    source: str
    target: str
    source_columns: tuple[str, ...]
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class Model:
    name: str
    description: str
    instructions: str
    datasets: tuple[Dataset, ...]
    relationships: tuple[Relationship, ...]
    # Model-scoped metrics first, then each dataset's metrics as DATASET.metric.
    metrics: tuple[Metric, ...]
    synonyms: dict = field(default_factory=dict, compare=False)
    examples: tuple[str, ...] = ()

    def dataset(self, name):
        return find(self.datasets, name)

    def metric(self, name):
        return next((m for m in self.metrics if m.name == name), None) or find(self.metrics, name)


def text(value, limit=20000):
    return value[:limit] if isinstance(value, str) else ""


def dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def strings(value):
    return tuple(text(v) for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def expression(item):
    """The SQL of a field or metric and its dialect: the best of `sql` (OSSIE_SQL_2026) and its `dialects`."""
    found = item.get("expression")
    if not isinstance(found, dict):
        return "", ""
    options = {}
    for d in dicts(found.get("dialects")):
        options.setdefault(text(d.get("dialect")).upper(), text(d.get("expression")).strip())
    if text(found.get("sql")).strip():
        options[PORTABLE] = text(found["sql"]).strip()  # The top-level sql wins over an OSSIE_SQL_2026 entry.
    return next(((options[d], d) for d in DIALECTS if options.get(d)), ("", ""))


def instructions(context):
    """Ossie's `ai_context` is an object such as {instructions, synonyms, examples}, or plain text."""
    if isinstance(context, str):
        return context[:8000], {}, ()
    if not isinstance(context, dict):
        return "", {}, ()
    synonyms = context.get("synonyms") if isinstance(context.get("synonyms"), dict) else {}
    found = context.get("examples")
    examples = tuple(e.strip()[:300] for e in found if isinstance(e, str) and e.strip())[:MAX_EXAMPLES] \
        if isinstance(found, list) else ()
    return text(context.get("instructions"), 8000), dict(synonyms), examples


def entity_synonyms(item):
    """Ossie's synonyms of one field or metric: a list of strings in its own `ai_context`."""
    context = item.get("ai_context")
    found = context.get("synonyms") if isinstance(context, dict) else None
    return [text(s, 200) for s in found[:20] if isinstance(s, str)] if isinstance(found, list) else []


def dataset_target(source):
    """(namespace levels, table) of a dataset's `source`, `<namespace>….<table>` in the model's catalog.

    Polaris resolves every source this way when it stores a model, and refuses one that is not a table or view.
    """
    parts = text(source).split(".")
    return (tuple(parts[:-1]), parts[-1]) if len(parts) >= 2 and all(parts) else None


def names(kind, items, scope="The semantic model"):
    seen = set()
    for item in items:
        if not NAME.match(item.name.rpartition(".")[2]):
            raise CbiError(422, f"{scope} has a {kind} with an unsupported name: {item.name[:60]!r}.",
                                "invalid_model")
        if key(item.name) in seen:
            raise CbiError(422, f"{scope} names {kind} {item.name!r} twice.", "invalid_model")
        seen.add(key(item.name))


def limit(kind, items, most):
    if len(items) > most:
        raise CbiError(422, f"The semantic model has {len(items)} {kind}; Conversational BI reads at most {most}.",
                            "invalid_model")


def dimension(field):
    """Ossie marks a dimension with an object, `{"is_time": bool}`.

    None when the model says nothing about the field, so unflagged models keep every field usable.
    """
    return True if isinstance(field.get("dimension"), dict) else None


def document_of(loaded):
    """The Ossie document of a Polaris semantic-model response, or of a bare Ossie document."""
    raw = loaded.get("document", {}).get("semantic_model") if isinstance(loaded, dict) and "document" in loaded \
        else loaded
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) else None


def metric_of(m, dataset=None):
    sql, dialect = expression(m)
    name = text(m.get("name"))
    return Metric(name=f"{dataset}.{name}" if dataset else name, expression=sql, dialect=dialect,
                  description=text(m.get("description"), 2000), datatype=text(m.get("datatype"), 60), dataset=dataset)


def parse(loaded):
    """A Polaris semantic-model response (or a bare Ossie document) as a `Model`."""
    model = document_of(loaded)
    if model is None or not dicts(model.get("datasets")):
        raise CbiError(422, "The stored semantic model has no datasets.", "invalid_model")
    limit("datasets", dicts(model.get("datasets")), MAX_DATASETS)
    limit("relationships", dicts(model.get("relationships")), MAX_RELATIONSHIPS)
    scoped = [m for d in dicts(model.get("datasets")) for m in dicts(d.get("metrics"))]
    limit("metrics", dicts(model.get("metrics")) + scoped, MAX_METRICS)
    datasets = []
    for d in dicts(model.get("datasets")):
        name = text(d.get("name"))
        limit(f"fields in dataset {name[:60]!r}", dicts(d.get("fields")), MAX_FIELDS)
        target = dataset_target(d.get("source"))
        if target is None:
            raise CbiError(422, f"Dataset {name[:60]!r} needs a source like namespace.table.", "invalid_model")
        fields = tuple(
            Field(name=text(f.get("name")), expression=(found := expression(f))[0], dialect=found[1],
                  description=text(f.get("description"), 2000), datatype=text(f.get("datatype"), 60),
                  dimension=dimension(f))
            for f in dicts(d.get("fields"))
        )
        metrics = tuple(metric_of(m, name) for m in dicts(d.get("metrics")))
        # Fields and dataset-scoped metrics share one namespace.
        names("field or metric", [*fields, *(Field(m.local, "") for m in metrics)], f"Dataset {name[:60]!r}")
        unique = d.get("unique_keys")
        datasets.append(Dataset(
            name=name, namespace=target[0], table=target[1], source=text(d.get("source")),
            description=text(d.get("description"), 2000), fields=fields, metrics=metrics,
            primary_key=strings(d.get("primary_key")),
            unique_keys=tuple(strings(k) for k in unique if isinstance(k, list)) if isinstance(unique, list) else (),
        ))
    relationships = tuple(
        Relationship(name=text(r.get("name")), source=text(r.get("from")), target=text(r.get("to")),
                     source_columns=strings(r.get("from_columns")), target_columns=strings(r.get("to_columns")))
        for r in dicts(model.get("relationships"))
    )
    metrics = tuple(metric_of(m) for m in dicts(model.get("metrics")))
    names("dataset", datasets)
    names("relationship", relationships)
    names("metric", metrics)
    by_name = {key(d.name): d for d in datasets}
    for r in relationships:
        if key(r.source) not in by_name or key(r.target) not in by_name:
            raise CbiError(422, f"Relationship {r.name!r} joins a dataset the model does not define.",
                                "invalid_model")
        if not r.source_columns or len(r.source_columns) != len(r.target_columns):
            raise CbiError(422, f"Relationship {r.name!r} needs matching from_columns and to_columns.",
                                "invalid_model")
        ends = ((by_name[key(r.source)], r.source_columns), (by_name[key(r.target)], r.target_columns))
        for dataset, columns in ends:
            if missing := [c for c in columns if dataset.field(c) is None]:
                raise CbiError(422, f"Relationship {r.name!r} names {', '.join(missing)}, which is not a field "
                                         f"of {dataset.name}.", "invalid_model")
    text_instructions, synonyms, examples = instructions(model.get("ai_context"))
    # Per-entity synonyms join the model-level map, keyed as agents name them: DATASET.field or the metric.
    entities = [(f"{text(d.get('name'))}.{text(f.get('name'))}", f)
                for d in dicts(model.get("datasets")) for f in dicts(d.get("fields"))]
    scoped = [(f"{text(d.get('name'))}.{text(m.get('name'))}", m)
              for d in dicts(model.get("datasets")) for m in dicts(d.get("metrics"))]
    for name, item in [*entities, *((text(m.get("name")), m) for m in dicts(model.get("metrics"))), *scoped]:
        if found := entity_synonyms(item):
            synonyms.setdefault(name, found)
    return Model(name=text(model.get("name"), 200), description=text(model.get("description"), 4000),
                 instructions=text_instructions, datasets=tuple(datasets), relationships=relationships,
                 metrics=metrics + tuple(m for d in datasets for m in d.metrics), synonyms=synonyms,
                 examples=examples)
