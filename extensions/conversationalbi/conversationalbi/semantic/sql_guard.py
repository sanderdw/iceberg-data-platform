"""SQL an LLM wrote, for the Evaluations control arm only: checked against an allowlist before it reaches DuckDB.

The chat never runs SQL an LLM wrote. The control arm measures what a semantic model adds, so there an
LLM gets the table schemas and writes its own, when a platform administrator set BI_EVAL_SCHEMA_ONLY.
The worker is locked down, but it has httpfs loaded for the vended storage, so this check is what keeps a
query to the bound tables: one SELECT, only those tables, and no function outside the allowlist of
`expressions.py`. The query that runs is the one regenerated from the checked tree, never the LLM's text.
"""

import sqlglot
from sqlglot import exp

from ..errors import CbiError
from .expressions import ALLOWED, DIALECT

QUERY = (exp.Select, exp.From, exp.Join, exp.With, exp.CTE, exp.Group, exp.Having, exp.Order, exp.Ordered, exp.Limit,
         exp.Offset, exp.Subquery, exp.Alias, exp.TableAlias, exp.Union, exp.Except, exp.Intersect, exp.Window,
         exp.WindowSpec, exp.RowNumber, exp.Rank, exp.Table, exp.Star, exp.Exists)
MAX_SQL = 20_000


def refuse(message):
    return CbiError(422, message, "unsafe_sql")


def check(sql, views):
    """The query to run, regenerated from its checked syntax tree, or CbiError `unsafe_sql` saying why not."""
    if not sql or len(sql) > MAX_SQL:
        raise refuse("The SQL is empty or too long.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except sqlglot.errors.SqlglotError:
        raise refuse("This SQL could not be read. Write one DuckDB SELECT statement.") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select | exp.SetOperation):
        raise refuse("Run exactly one SELECT statement.")
    tree = statements[0]
    known = {v.lower() for v in views} | {c.alias.lower() for c in tree.find_all(exp.CTE)}
    for node in tree.walk():
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier) or node.db or node.catalog or node.name.lower() not in known:
                raise refuse(f"Only these tables can be read: {', '.join(sorted(views))}.")
        elif not isinstance(node, ALLOWED + QUERY) or isinstance(node, exp.Anonymous):
            raise refuse(f"{type(node).__name__} is not allowed here. Use standard SQL on the listed tables.")
    return tree.sql(dialect=DIALECT)
