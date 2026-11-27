"""Ossie name resolution: field and metric SQL as expressions over the columns of the source tables.

A field expression reads columns of its dataset's `source`. A dataset-scoped metric reads that
dataset's fields by plain name and its source columns as `source.column`. A model-scoped metric
reads fields as `dataset.field` and evaluates a dataset-scoped metric with `AGG(dataset.metric)`.
Every reference is replaced by what it means, so each column of a resolved expression is a source
column qualified with its dataset's name, ready to be pointed at a join alias.

Identifiers compare as Ossie specifies: a regular identifier without regard to case, a quoted one
in OSSIE_SQL_2026 exactly as written against the upper-cased declared name. Vendor dialects compare
without regard to case.
"""

from dataclasses import dataclass

from sqlglot import exp

from ..errors import CbiError
from . import expressions
from .ossie import PORTABLE, key


@dataclass(frozen=True)
class Resolved:
    tree: exp.Expression
    datasets: frozenset[str]


def invalid(message):
    return CbiError(422, message, "invalid_model")


def same(declared, identifier, strict):
    """Whether an identifier in SQL names something declared in the model as `declared`."""
    if identifier.args.get("quoted") and strict:
        return identifier.name == key(declared)
    return identifier.name.upper() == key(declared)


def lookup(items, identifier, strict, name=lambda item: item.name):
    return next((i for i in items if same(name(i), identifier, strict)), None)


def local(metric):
    return metric.local


def replace(tree, node, new):
    """Replace `node` in `tree`, which may be `tree` itself; returns the tree."""
    if node is tree:
        return new
    node.replace(new)
    return tree


def source_qualifier(dataset, identifier):
    """`source.column`: the regular identifier SOURCE, unless the dataset declares a field named source."""
    return not identifier.args.get("quoted") and identifier.name.upper() == "SOURCE" and dataset.field("source") is None


def wrap(tree):
    """The tree, parenthesised unless it is one column, value, call or parenthesised expression."""
    return tree if isinstance(tree, exp.Column | exp.Literal | exp.Paren | exp.Func) else exp.Paren(this=tree)


def field(dataset, item):
    """A field's expression with its columns qualified by the dataset's name."""
    label = f"{dataset.name}.{item.name}"
    if not item.expression:
        raise invalid(f"Field {label} has no expression.")
    tree = expressions.parse(item.expression)
    if expressions.is_aggregate(tree):
        raise invalid(f"Field {label} is an aggregate; a field is a value of each row. Make it a metric.")
    for column in list(tree.find_all(exp.Column)):
        parts = column.parts
        if len(parts) > 2 or (len(parts) == 2 and key(parts[0].name) != key(dataset.name)):
            raise invalid(f"Field {label} reads {column.sql()}; a field reads only columns of its own table.")
        column.set("table", exp.to_identifier(dataset.name))
        column.set("db", None)
        column.set("catalog", None)
    return tree


def column_of(dataset, column):
    return exp.Column(this=column.this.copy(), table=exp.to_identifier(dataset.name))


def dataset_metric(model, dataset, metric, tree):
    """Rules 1 to 3 and 7 of Ossie's metric scoping, for a metric declared on `dataset`."""
    strict = metric.dialect == PORTABLE
    for agg in list(tree.find_all(exp.Agg)):
        argument = agg.this
        parts = argument.parts if isinstance(argument, exp.Column) else []
        if len(parts) == 2 and source_qualifier(dataset, parts[0]):
            raise CbiError(422, f"Metric {metric.name} evaluates the measure {argument.sql()}, but "
                                     f"{dataset.name} reads an Iceberg table, which has no measures. Aggregate its "
                                     f"columns instead, such as SUM(source.{argument.name}).", "no_measures")
        raise invalid(f"Metric {metric.name}: a dataset-scoped metric calls AGG only as AGG(source.measure).")
    for column in list(tree.find_all(exp.Column)):
        parts = column.parts
        if len(parts) == 1:
            found = lookup(dataset.fields, parts[0], strict)
            if found is None:
                other = lookup(dataset.metrics, parts[0], strict, local)
                raise invalid(f"Metric {metric.name} reads {column.sql()}, " + (
                    "another metric; a metric cannot refer to a metric." if other else
                    f"which is not a field of {dataset.name}. Write source.{column.name} for a column of its table."))
            tree = replace(tree, column, wrap(field(dataset, found)))
        elif len(parts) == 2 and source_qualifier(dataset, parts[0]):
            tree = replace(tree, column, column_of(dataset, column))
        else:
            raise invalid(f"Metric {metric.name} reads {column.sql()}. A metric on {dataset.name} reads its fields by "
                          "name and its table's columns as source.column; a metric that reads other datasets belongs "
                          "in the model's metrics.")
    return Resolved(tree, frozenset({dataset.name}))


def model_metric(model, metric, tree):
    """Rules 5 and 6 of Ossie's metric scoping: `dataset.field`, and `AGG(dataset.metric)`."""
    strict = metric.dialect == PORTABLE
    used = set()
    columns = [c for c in tree.find_all(exp.Column) if c.find_ancestor(exp.Agg) is None]
    for column in columns:
        parts = column.parts
        if len(parts) != 2:
            raise invalid(f"Metric {metric.name} reads {column.sql()}. A model's metric names fields as "
                          "dataset.field.")
        dataset = lookup(model.datasets, parts[0], strict)
        if dataset is None:
            raise invalid(f"Metric {metric.name} reads {column.sql()}, but the model has no dataset {parts[0].name}.")
        found = lookup(dataset.fields, parts[1], strict)
        if found is None:
            hint = (f" Evaluate a metric with AGG({column.sql()})."
                    if lookup(dataset.metrics, parts[1], strict, local) else "")
            raise invalid(f"Metric {metric.name} reads {column.sql()}, which is not a field of {dataset.name}.{hint}")
        tree = replace(tree, column, wrap(field(dataset, found)))
        used.add(dataset.name)
    for agg in list(tree.find_all(exp.Agg)):
        argument = agg.this
        if isinstance(agg.parent, exp.Filter) and agg.parent.this is agg:
            raise invalid(f"Metric {metric.name}: AGG takes no FILTER (WHERE ...) clause.")
        dataset = scoped = None
        if isinstance(argument, exp.Column) and len(argument.parts) == 2:
            dataset = lookup(model.datasets, argument.parts[0], strict)
            scoped = lookup(dataset.metrics, argument.parts[1], strict, local) if dataset else None
        if scoped is None:
            raise invalid(f"Metric {metric.name}: AGG evaluates a dataset-scoped metric, written "
                          f"AGG(dataset.metric); {argument.sql()} is not one.")
        inner = dataset_metric(model, dataset, scoped, expressions.parse(scoped.expression))
        tree = replace(tree, agg, wrap(inner.tree))
        used |= inner.datasets
    return Resolved(tree, frozenset(used))


def metric(model, item):
    """A metric's expression with every field, source column and AGG replaced by source-column SQL."""
    if not item.expression:
        raise invalid(f"Metric {item.name!r} has no SQL expression.")
    tree = expressions.parse(item.expression)
    if item.dataset:
        return dataset_metric(model, model.dataset(item.dataset), item, tree)
    return model_metric(model, item, tree)
