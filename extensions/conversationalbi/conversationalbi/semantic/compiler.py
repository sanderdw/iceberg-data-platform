"""Governed semantic queries: metrics by dimensions, compiled from an Ossie model into DuckDB SQL.

The model decides everything that makes an answer right or wrong: metric SQL, join keys and
which fields exist. A caller (a person, an AI agent or a script) only picks names:

    metrics     one or more metric names, all anchored on the same dataset (the metric's home)
    dimensions  `DATASET.field`, optionally with a time grain and the relationships to follow
    filters     on a dimension field (WHERE) or a metric (HAVING), with typed parameters

Joins follow relationships from the metric's home in their declared direction only, and only
when the relationship's `to_columns` are the target dataset's primary key. Every join is then
many-to-one, so it can never multiply rows and inflate a metric (a "fan-out"). A dataset that is
reachable along several paths, such as an airport of departure or of destination, needs `via`.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import CbiError
from . import expressions

Grain = Literal["day", "week", "month", "quarter", "year"]
Operator = Literal["=", "!=", "<", "<=", ">", ">=", "in", "not_in", "between", "is_null", "is_not_null"]
Scalar = str | int | float | bool
Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
FieldRef = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}\.[A-Za-z_][A-Za-z0-9_]{0,127}$",
                                description="DATASET.field, as listed by describe_model.")]
TIME_TYPES = ("date", "timestamp", "timestamptz", "timestamp_ns", "timestamptz_ns")
DUCKDB_TYPES = {"string": "VARCHAR", "int": "INTEGER", "long": "BIGINT", "float": "FLOAT", "double": "DOUBLE",
                "boolean": "BOOLEAN", "date": "DATE", "timestamp": "TIMESTAMP", "timestamptz": "TIMESTAMPTZ",
                "timestamp_ns": "TIMESTAMP_NS", "timestamptz_ns": "TIMESTAMPTZ", "uuid": "UUID", "time": "TIME"}
# Ossie's logical datatypes as the Iceberg types this module reads; Iceberg names pass through.
OSSIE_TYPES = {"datetime": "timestamp", "datetimetz": "timestamptz", "integer": "long", "float": "double",
               "decimal": "double", "opaque": ""}
MAX_DEPTH = 4
UNIT = re.compile(r"\bUnit:\s*([^.;\n]{1,40})", re.IGNORECASE)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(Input):
    database: str = Field(pattern=r"^db-[a-f0-9]{32}$", description="Database id, from list_models.")
    namespace: list[Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")]] = Field(
        min_length=1, max_length=20)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,256}$", description="Semantic model name.")

    @property
    def key(self):
        return f"{self.database}/{'.'.join(self.namespace)}/{self.name}"


class Dimension(Input):
    field: FieldRef
    via: list[Name] = Field(default=[], max_length=MAX_DEPTH,
                            description="Relationship names that pick the join path when several exist.")
    grain: Grain | None = Field(default=None, description="Truncate a date or timestamp field.")


class Filter(Input):
    field: FieldRef | None = None
    metric: Name | None = None
    via: list[Name] = Field(default=[], max_length=MAX_DEPTH)
    op: Operator
    value: Scalar | list[Scalar] | None = None

    @model_validator(mode="after")
    def target(self):
        if (self.field is None) == (self.metric is None):
            raise ValueError("A filter names either a field or a metric.")
        listed = isinstance(self.value, list)
        if self.op in ("is_null", "is_not_null"):
            if self.value is not None:
                raise ValueError(f"{self.op} takes no value.")
        elif self.op in ("in", "not_in"):
            if not listed or not 1 <= len(self.value) <= 100:
                raise ValueError(f"{self.op} needs a list of 1 to 100 values.")
        elif self.op == "between":
            if not listed or len(self.value) != 2:
                raise ValueError("between needs a list of two values.")
        elif listed or self.value is None:
            raise ValueError(f"{self.op} needs one value.")
        if self.metric and self.op in ("in", "not_in", "is_null", "is_not_null"):
            raise ValueError("A metric filter compares with =, !=, <, <=, >, >= or between.")
        return self


class Order(Input):
    name: str = Field(max_length=300, description="An output column: a metric name or a dimension's column name.")
    desc: bool = False


class QuerySpec(Input):
    metrics: list[Name] = Field(min_length=1, max_length=5)
    dimensions: list[Dimension] = Field(default=[], max_length=5)
    filters: list[Filter] = Field(default=[], max_length=10)
    order_by: list[Order] = Field(default=[], max_length=5)
    limit: int = Field(default=100, ge=1, le=5000)


class QueryInput(QuerySpec):
    model: ModelRef


@dataclass(frozen=True)
class Compiled:
    sql: str
    params: list
    bindings: list[dict]
    join_paths: dict[str, list[str]]
    columns: list[dict]
    metrics: list[dict]
    limit: int

    @property
    def pretty(self):
        return expressions.pretty(self.sql)


def fail(status, message, code, detail=None):
    return CbiError(status, message, code, detail)


def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def unit(description):
    found = UNIT.search(description or "")
    return found.group(1).strip() if found else None


def safe_edges(model):
    """Relationships that are many-to-one: they end on the target dataset's whole primary key."""
    edges = defaultdict(list)
    for r in model.relationships:
        target = model.dataset(r.target)
        if target.primary_key and sorted(r.target_columns) == sorted(target.primary_key):
            edges[r.source].append(r)
    return edges


def paths(model, home, target):
    """Every join path from `home` to `target`, as tuples of relationships, shortest first."""
    edges, found = safe_edges(model), []

    def walk(dataset, path, seen):
        if dataset == target and path:
            found.append(tuple(path))
            return
        if len(path) >= MAX_DEPTH:
            return
        for r in edges.get(dataset, []):
            if r.target not in seen:
                walk(r.target, [*path, r], seen | {r.target})

    if home == target:
        return [()]
    walk(home, [], {home})
    return sorted(found, key=lambda p: (len(p), [r.name for r in p]))


def choose_path(model, home, dataset, via, label):
    """The join path for one dimension or filter field, or a clear refusal."""
    options = paths(model, home, dataset)
    if via:
        options = [p for p in options if all(name in [r.name for r in p] for name in via)]
    names = [[r.name for r in p] for p in options]
    if not options:
        # The dataset relates to the home only against the join direction: joining it would repeat home rows.
        linked = any({r.source, r.target} & {dataset} for r in model.relationships)
        if linked and not via:
            raise fail(422, f"{label} cannot be combined with metrics on {home}: joining {dataset} would repeat "
                            f"{home} rows and inflate the metrics. Ask for a metric on {dataset} instead, or filter "
                            "separately.", "fan_out", {"field": label, "home": home})
        raise fail(422, f"No join path from {home} to {dataset}" + (f" through {', '.join(via)}." if via else "."),
                   "no_path", {"field": label, "home": home})
    shortest = [p for p in options if len(p) == len(options[0])]
    if len(shortest) > 1:
        raise fail(422, f"{label} can be reached from {home} in several ways. Name the relationship to follow in "
                        f"`via`: {'; '.join(' → '.join(n) for n in names[:len(shortest)])}.", "ambiguous_join",
                   {"field": label, "options": names[:len(shortest)]})
    return options[0]


def metric_home(model, metric, tree):
    homes = {name for name in expressions.datasets(tree) if model.dataset(name)}
    unknown = expressions.datasets(tree) - homes
    if unknown:
        raise fail(422, f"Metric {metric.name!r} reads {', '.join(sorted(unknown))}, which the model does not define.",
                   "invalid_model")
    if len(homes) == 1:
        return homes.pop()
    if not homes and len(model.datasets) == 1:
        return model.datasets[0].name
    if not homes:
        return None
    raise fail(422, f"Metric {metric.name!r} combines columns of {', '.join(sorted(homes))}; this layer only runs "
                    "metrics anchored on one dataset.", "metric_unanchored", {"metric": metric.name})


def dimension_fields(dataset):
    """Fields usable as dimensions: the flagged ones when the model flags any, otherwise all."""
    flagged = [f for f in dataset.fields if f.dimension]
    return flagged or [f for f in dataset.fields if f.dimension is not False]


def field_type(dataset, field, schemas):
    """The Iceberg type of a field that is a plain column, otherwise the model's declared datatype."""
    columns = schemas.get(dataset.name, {})
    if field.expression in columns:
        return columns[field.expression]
    declared = (field.datatype or "").lower()
    return OSSIE_TYPES.get(declared, declared) or None


def resolve_field(model, ref):
    dataset_name, _, field_name = ref.partition(".")
    dataset = model.dataset(dataset_name)
    if dataset is None:
        raise fail(422, f"The model has no dataset {dataset_name!r}.", "unknown_field",
                   {"datasets": [d.name for d in model.datasets]})
    field = next((f for f in dimension_fields(dataset) if f.name == field_name), None)
    if field is None:
        raise fail(422, f"{dataset_name} has no dimension {field_name!r}.", "unknown_field",
                   {"fields": [f.name for f in dimension_fields(dataset)]})
    return dataset, field


class Plan:
    """Join aliases for the paths one query needs."""

    def __init__(self, home):
        self.home = home
        self.paths = {(): home}

    def add(self, path):
        for i in range(1, len(path) + 1):
            self.paths.setdefault(tuple(path[:i]), path[i - 1].target)
        return tuple(path)

    def aliases(self):
        reached = defaultdict(set)
        for prefix, dataset in self.paths.items():
            reached[dataset].add(prefix)
        return {prefix: dataset if len(reached[dataset]) == 1 and (prefix == () or dataset != self.home)
                else f"{dataset}__{prefix[-1].name}"
                for prefix, dataset in self.paths.items()}

    def joins(self, aliases):
        clauses = []
        for prefix in sorted(self.paths, key=lambda p: (len(p), [r.name for r in p])):
            if not prefix:
                continue
            r, parent, alias = prefix[-1], aliases[prefix[:-1]], aliases[prefix]
            on = " AND ".join(f"{quote(parent)}.{quote(a)} = {quote(alias)}.{quote(b)}"
                              for a, b in zip(r.source_columns, r.target_columns, strict=True))
            clauses.append(f"LEFT JOIN {quote(r.target)} AS {quote(alias)} ON {on}")
        return clauses


def typed(kind):
    duck = DUCKDB_TYPES.get((kind or "").split("(")[0])
    if kind and kind.startswith("decimal"):
        duck = kind.upper()
    return f"CAST(? AS {duck})" if duck else "?"


def compile_query(model, schemas, spec, max_rows=5000):
    """`schemas`: dataset name → {column: Iceberg type}. Returns the SQL, its parameters and metadata."""
    metrics = []
    for name in spec.metrics:
        metric = model.metric(name)
        if metric is None:
            raise fail(422, f"The model has no metric {name!r}.", "unknown_metric",
                       {"metrics": [m.name for m in model.metrics]})
        if not metric.expression:
            raise fail(422, f"Metric {name!r} has no SQL expression.", "invalid_model")
        tree = expressions.parse(metric.expression)
        if not expressions.is_aggregate(tree):
            raise fail(422, f"Metric {name!r} is not an aggregate.", "invalid_model")
        metrics.append((metric, tree, metric_home(model, metric, tree)))
    homes = {home for *_, home in metrics if home}
    if len(homes) > 1:
        raise fail(422, "These metrics belong to different datasets (" + ", ".join(sorted(homes)) + "). Ask for them "
                        "in separate queries.", "mixed_grain")
    if not homes:
        raise fail(422, "None of these metrics names the dataset it counts. Add a metric that reads a dataset's "
                        "columns.", "metric_unanchored")
    home = homes.pop()
    plan = Plan(home)

    def place(ref, via):
        dataset, field = resolve_field(model, ref)
        path = plan.add(choose_path(model, home, dataset.name, via, ref))
        return dataset, field, path

    dims = [(d, *place(d.field, d.via)) for d in spec.dimensions]
    field_filters = [(f, *place(f.field, f.via)) for f in spec.filters if f.field]
    aliases = plan.aliases()

    def field_sql(dataset, field, path):
        tree = expressions.parse(field.expression)
        if expressions.is_aggregate(tree):
            raise fail(422, f"{dataset.name}.{field.name} is an aggregate and cannot be a dimension.", "invalid_model")
        if expressions.datasets(tree) - {dataset.name}:
            raise fail(422, f"{dataset.name}.{field.name} reads another dataset.", "invalid_model")
        return expressions.sql(expressions.qualify(tree, {dataset.name: aliases[path]}, default=dataset.name))

    select, columns, join_paths, outputs = [], [], {}, []
    for d, dataset, field, path in dims:
        expr, kind = field_sql(dataset, field, path), field_type(dataset, field, schemas)
        alias = aliases[path]
        name = f"{alias}.{field.name}" + (f":{d.grain}" if d.grain else "")
        if d.grain:
            if kind not in TIME_TYPES:
                raise fail(422, f"{d.field} is not a date or timestamp, so it has no {d.grain} grain.", "invalid_grain")
            expr = f"date_trunc('{d.grain}', {expr})"
            kind = "date" if d.grain != "day" or kind == "date" else kind
        if name in outputs:
            raise fail(422, f"{name} is asked for twice.", "invalid_request")
        outputs.append(name)
        select.append(f"{expr} AS {quote(name)}")
        join_paths[name] = [r.name for r in path]
        columns.append({"name": name, "kind": "dimension", "dataset": dataset.name, "field": field.name,
                        "label": field.name.replace("_", " ") + (f" ({d.grain})" if d.grain else ""),
                        "via": [r.name for r in path], "type": kind, "grain": d.grain,
                        "description": field.description})
    metric_meta = []
    for metric, tree, _ in metrics:
        expr = expressions.sql(expressions.qualify(tree, {home: aliases[()]}, default=home))
        select.append(f"{expr} AS {quote(metric.name)}")
        outputs.append(metric.name)
        meta = {"name": metric.name, "description": metric.description, "expression": metric.expression,
                "unit": unit(metric.description), "dataset": home}
        metric_meta.append(meta)
        columns.append({"name": metric.name, "kind": "metric", "label": metric.name.replace("_", " "),
                        "unit": meta["unit"], "additive": expressions.is_additive(tree),
                        "description": metric.description})

    params, where, having = [], [], []

    def condition(expr, flt, kind):
        placeholder = typed(kind)
        if flt.op == "is_null":
            return f"{expr} IS NULL"
        if flt.op == "is_not_null":
            return f"{expr} IS NOT NULL"
        if flt.op in ("in", "not_in"):
            params.extend(flt.value)
            return f"{expr} {'NOT IN' if flt.op == 'not_in' else 'IN'} ({', '.join([placeholder] * len(flt.value))})"
        if flt.op == "between":
            params.extend(flt.value)
            return f"{expr} BETWEEN {placeholder} AND {placeholder}"
        params.append(flt.value)
        return f"{expr} {flt.op} {placeholder}"

    for flt, dataset, field, path in field_filters:
        where.append(condition(field_sql(dataset, field, path), flt, field_type(dataset, field, schemas)))
    for flt in (f for f in spec.filters if f.metric):
        metric = model.metric(flt.metric)
        if metric is None:
            raise fail(422, f"The model has no metric {flt.metric!r}.", "unknown_metric")
        tree = expressions.parse(metric.expression)
        if metric_home(model, metric, tree) not in (home, None):
            raise fail(422, f"Metric {flt.metric!r} belongs to another dataset.", "mixed_grain")
        having.append(condition(expressions.sql(expressions.qualify(tree, {home: aliases[()]}, default=home)),
                                flt, None))

    order = []
    for o in spec.order_by:
        if o.name not in outputs:
            raise fail(422, f"Order by an output column: {', '.join(outputs)}.", "invalid_order")
        order.append(f"{quote(o.name)} {'DESC' if o.desc else 'ASC'} NULLS LAST")
    if not order:
        order = [f"{quote(name)} ASC NULLS LAST" for name in outputs[:len(dims)]]
    limit = min(spec.limit, max_rows)
    query = " ".join([
        "SELECT " + ", ".join(select),
        f"FROM {quote(home)} AS {quote(aliases[()])}",
        *plan.joins(aliases),
        *(["WHERE " + " AND ".join(where)] if where else []),
        *(["GROUP BY ALL"] if dims else []),
        *(["HAVING " + " AND ".join(having)] if having else []),
        *(["ORDER BY " + ", ".join(order)] if order else []),
        f"LIMIT {limit + 1}",
    ])
    used = sorted(set(plan.paths.values()))
    bindings = [{"view": name, "namespace": list(model.dataset(name).namespace), "table": model.dataset(name).table}
                for name in used]
    return Compiled(sql=query, params=params, bindings=bindings, join_paths=join_paths, columns=columns,
                    metrics=metric_meta, limit=limit)


def reachable(model, home):
    """For describe_model: each dimension reachable from a metric home, with its join path or options."""
    found = []
    for dataset in model.datasets:
        options = paths(model, home, dataset.name)
        shortest = [p for p in options if options and len(p) == len(options[0])]
        for field in dimension_fields(dataset):
            entry = {"field": f"{dataset.name}.{field.name}"}
            if not options:
                continue
            if len(shortest) > 1:
                entry["via"] = [[r.name for r in p] for p in shortest]
                entry["ambiguous"] = True
            else:
                entry["path"] = [r.name for r in options[0]]
            found.append(entry)
    return found
