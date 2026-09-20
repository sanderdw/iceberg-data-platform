"""Versioned portal definitions and a small, explicit read-only SQL language."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from user_portal.preview import sql_identifier

Name = Annotated[str, Field(min_length=1, max_length=256)]
Scalar = str | int | float | bool | None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Input):
    database: Name
    namespace: list[Name] = Field(min_length=1, max_length=20)
    table: Name

    @field_validator("namespace")
    @classmethod
    def namespace_parts(cls, value):
        if any(p in (".", "..") or "\x1f" in p or "\0" in p for p in value):
            raise ValueError("Invalid namespace.")
        return value


class Filter(Input):
    column: Name
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "null", "not_null"] = "eq"
    value: Scalar = None

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value):
        if (
            isinstance(value, str)
            and len(value) > 512
            or isinstance(value, float)
            and not math.isfinite(value)
        ):
            raise ValueError("Filter value is too large or not finite.")
        return value


class Dimension(Input):
    column: Name
    grain: Literal["none", "hour", "day", "week", "month", "year"] = "none"


class Builder(Input):
    aggregate: Literal["rows", "count", "sum", "avg", "min", "max", "distinct"] = "count"
    measure: str = Field(default="", max_length=256)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=2)
    columns: list[Name] = Field(default_factory=list, max_length=20)
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    sort: str = Field(default="", max_length=256)
    descending: bool = False
    limit: int = Field(default=500, ge=1, le=1000)

    @model_validator(mode="after")
    def measure_required(self):
        if self.aggregate not in ("rows", "count") and not self.measure:
            raise ValueError("Select a measure.")
        if self.aggregate == "rows" and not self.columns:
            raise ValueError("Select columns for the table.")
        return self


class Visualization(Input):
    kind: Literal["table", "kpi", "bar", "line", "area", "scatter"] = "table"
    x: str = Field(default="dimension", max_length=256)
    y: str = Field(default="value", max_length=256)
    color: str = Field(default="", max_length=256)


class Report(Input):
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    source: Source
    mode: Literal["builder", "sql"] = "builder"
    builder: Builder = Field(default_factory=Builder)
    sql: str = Field(default="", max_length=8192)
    parameters: dict[str, Scalar] = Field(default_factory=dict, max_length=12)
    visualization: Visualization = Field(default_factory=Visualization)
    timezone: Literal["UTC", "Europe/Amsterdam"] = "Europe/Amsterdam"

    @field_validator("parameters")
    @classmethod
    def valid_parameters(cls, value):
        import re

        if any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", k) for k in value):
            raise ValueError("Use simple names for SQL parameters.")
        for item in value.values():
            Filter.bounded_value(item)
        return value


class Card(Input):
    report_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    width: Literal["half", "full"] = "half"
    filter_column: str = Field(default="", max_length=256)


class Dashboard(Input):
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    cards: list[Card] = Field(default_factory=list, max_length=8)
    filter_label: str = Field(default="Filter", min_length=1, max_length=60)


def filter_sql(filters, parameters):
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    terms = []
    for index, item in enumerate(filters):
        column = sql_identifier(item.column)
        if item.operator in ("null", "not_null"):
            terms.append(f"{column} IS {'NOT ' if item.operator == 'not_null' else ''}NULL")
        else:
            name = f"filter_{index}"
            parameters[name] = item.value
            terms.append(
                f"contains(CAST({column} AS VARCHAR), ${name})"
                if item.operator == "contains"
                else f"{column} {operators[item.operator]} ${name}"
            )
    return " AND ".join(terms)


def builder_sql(builder):
    parameters = {}
    if builder.aggregate == "rows":
        output = list(dict.fromkeys(builder.columns))
        selected = [sql_identifier(c) for c in output]
    else:
        output, selected = [], []
        for index, dimension in enumerate(builder.dimensions):
            name = ("dimension", "series")[index]
            value = sql_identifier(dimension.column)
            if dimension.grain != "none":
                value = f"date_trunc('{dimension.grain}', {value})"
            selected.append(f"{value} AS {name}")
            output.append(name)
        measure = sql_identifier(builder.measure)
        value = (
            "count(*)"
            if builder.aggregate == "count"
            else f"count(DISTINCT {measure})"
            if builder.aggregate == "distinct"
            else f"{builder.aggregate}({measure})"
        )
        selected.append(f"{value} AS value")
        output.append("value")
    sql = "SELECT " + ", ".join(selected) + " FROM source"
    where = filter_sql(builder.filters, parameters)
    if where:
        sql += " WHERE " + where
    if builder.aggregate != "rows" and builder.dimensions:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(builder.dimensions)))
    if builder.sort:
        if builder.sort not in output:
            raise ValueError("Choose an output column for sorting.")
        sql += f" ORDER BY {sql_identifier(builder.sort)} {'DESC' if builder.descending else 'ASC'}"
    sql += f" LIMIT {builder.limit}"
    return sql, parameters


# An allowlist is intentional: new DuckDB/sqlglot features are unavailable until
# reviewed. SELECT alone can still execute filesystem, network and secret APIs.
SQL_NODES = set(
    """Select From Table Identifier Column Alias Literal Star Where Group Having
Order Ordered Limit Offset CTE With Subquery Paren Distinct And Or Not EQ NEQ GT GTE LT LTE
Is Null Boolean Between In Like ILike Add Sub Mul Div Mod Neg Case If Cast TryCast DataType
DataTypeParam Interval Var Count Sum Avg Min Max Round Abs Coalesce Nullif Lower Upper Length
Trim Contains DateTrunc TimestampTrunc Date Extract Placeholder TableAlias Join Union
""".split()  # noqa: SIM905 - Keep the reviewed grammar easy to compare.
)


def validate_sql(sql, parameters):
    import sqlglot
    from sqlglot import exp

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        raise ValueError("Check the SQL syntax.") from None
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise ValueError("Use one read-only SELECT query.")
    tree = statements[0]
    if any(type(node).__name__ not in SQL_NODES for node in tree.walk()):
        raise ValueError(
            "This SQL feature is not supported. Use SELECT, filters, aggregates or CTEs over source."
        )
    ctes = [cte.alias.lower() for cte in tree.find_all(exp.CTE)]
    if "source" in ctes or len(ctes) != len(set(ctes)):
        raise ValueError("Use unique CTE names; source is reserved.")
    if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
        raise ValueError("Recursive queries are not supported.")
    for table in tree.find_all(exp.Table):
        if (
            not isinstance(table.this, exp.Identifier)
            or table.db
            or table.catalog
            or table.name.lower() not in {"source", *ctes}
        ):
            raise ValueError("Query only the selected table as source.")
    names = {str(p.this) for p in tree.find_all(exp.Placeholder)}
    if names != set(parameters):
        raise ValueError("Define a value for every named $parameter, and remove unused parameters.")
    return tree.sql(dialect="duckdb")


def report_sql(report):
    if report.mode == "builder":
        return builder_sql(report.builder)
    return validate_sql(report.sql, report.parameters), report.parameters
