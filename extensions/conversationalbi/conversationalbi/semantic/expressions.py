"""SQL expressions from a semantic model, checked against an allowlist before they reach DuckDB.

A model can come from another team through a data share, so its metric and field SQL is not
trusted. Only scalar and aggregate expressions over the model's own columns pass: no queries,
tables, table functions, file readers, settings or unknown functions. The worker that runs the
query has nothing beyond one read-only catalog token and vended storage credentials either.
"""

from functools import lru_cache

import sqlglot
from sqlglot import exp

from ..errors import CbiError

DIALECT = "duckdb"
AGGREGATES = (exp.Avg, exp.Sum, exp.Count, exp.Min, exp.Max, exp.Median, exp.Stddev, exp.StddevPop,
              exp.StddevSamp, exp.Variance, exp.VariancePop, exp.PercentileCont, exp.PercentileDisc,
              exp.ApproxDistinct, exp.CountIf, exp.LogicalAnd, exp.LogicalOr)
SCALARS = (exp.Nullif, exp.Coalesce, exp.Abs, exp.Round, exp.Floor, exp.Ceil, exp.Sqrt, exp.Ln, exp.Log, exp.Pow,
           exp.Greatest, exp.Least, exp.If, exp.Case, exp.Cast, exp.TryCast, exp.Extract, exp.DateDiff, exp.DateAdd,
           exp.DateSub, exp.DateTrunc, exp.TimestampTrunc, exp.TimeToUnix, exp.TimeToStr, exp.CurrentDate,
           exp.Upper, exp.Lower, exp.Length, exp.Concat, exp.Substring, exp.Trim, exp.DPipe, exp.Sign)
OPERATORS = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod, exp.Neg, exp.Paren, exp.EQ, exp.NEQ, exp.GT,
             exp.GTE, exp.LT, exp.LTE, exp.And, exp.Or, exp.Not, exp.Is, exp.In, exp.Between, exp.Like, exp.ILike,
             exp.Filter, exp.Where, exp.Distinct, exp.Interval, exp.Tuple)
LEAVES = (exp.Column, exp.Identifier, exp.Literal, exp.Boolean, exp.Null, exp.DataType, exp.DataTypeParam, exp.Var)
ALLOWED = AGGREGATES + SCALARS + OPERATORS + LEAVES


def refuse(message):
    return CbiError(422, message, "unsafe_expression")


@lru_cache(maxsize=2048)
def _parse(sql):
    if not sql or len(sql) > 4000 or ";" in sql:
        raise refuse("A semantic-model expression is empty, too long or has a ';'.")
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except sqlglot.errors.ParseError:
        raise refuse(f"Cannot read the expression {sql[:80]!r}.") from None
    for node in tree.walk():
        if isinstance(node, exp.Star):
            if not isinstance(node.parent, exp.Count):
                raise refuse("`*` is only allowed inside count(*).")
            continue
        if isinstance(node, exp.In) and (node.args.get("query") or node.args.get("unnest")):
            raise refuse("IN needs a list of values, not a query.")
        if not isinstance(node, ALLOWED) or isinstance(node, (exp.Anonymous, exp.Subquery, exp.Select, exp.Table)):
            raise refuse(f"The expression {sql[:80]!r} uses {type(node).__name__}, which is not allowed.")
    return tree


def parse(sql):
    """A checked copy of the expression's syntax tree."""
    return _parse(sql.strip()).copy()


def is_aggregate(tree):
    return any(isinstance(n, AGGREGATES) for n in tree.walk())


def is_additive(tree):
    """Whether a metric adds up across groups (a plain SUM or COUNT), so its bars can be stacked."""
    node = tree.this if isinstance(tree, exp.Filter) else tree
    return isinstance(node, (exp.Sum, exp.Count)) and node.find(exp.Distinct) is None


def datasets(tree):
    """The dataset names the expression's columns are qualified with."""
    return {c.table for c in tree.find_all(exp.Column) if c.table}


def columns(tree):
    return {(c.table, c.name) for c in tree.find_all(exp.Column)}


def qualify(tree, aliases, default=None):
    """Point every column at a join alias: `DATASET.col` through `aliases`, bare columns to `default`."""
    tree = tree.copy()
    for column in list(tree.find_all(exp.Column)):
        source = column.table or default
        alias = aliases.get(source)
        if alias is None:
            raise refuse(f"The expression reads {source or 'an unqualified column'}, which this query cannot join.")
        column.set("table", exp.to_identifier(alias, quoted=True))
        column.set("this", exp.to_identifier(column.name, quoted=True))
        column.set("db", None)
        column.set("catalog", None)
    return tree


def sql(tree):
    return tree.sql(dialect=DIALECT)


def pretty(query):
    try:
        return sqlglot.transpile(query, read=DIALECT, write=DIALECT, pretty=True)[0]
    except sqlglot.errors.SqlglotError:
        return query
