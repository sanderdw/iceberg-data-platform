"""The control arm of an evaluation: the same LLM with only the table schemas, writing its own SQL.

It measures what the semantic layer adds, so everything else matches the chat agent: temperature,
retries, the rows it may see and how it should behave. It has no descriptions, relationships, metrics
or guidance, and it is reachable only through the evaluation API, with BI_EVAL_SCHEMA_ONLY set.
"""

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from ..errors import CbiError
from ..semantic import sql_guard

MAX_ROWS = 1000
INSTRUCTIONS = [
    "You are a careful business analyst for the Iceberg Data Platform. You answer questions only with data from "
    "the tables below, using the `run_sql` tool. Never invent numbers, and never answer from general knowledge when "
    "the question is about the data.",
    "`run_sql` takes one DuckDB SELECT statement. When it returns an error, read it and correct the query. Ask the "
    "person which one they mean when the question does not say. When the tables cannot answer it, say so plainly.",
    "Then answer briefly in text: first one sentence with the finding, then what was measured and over what period.",
]


def tables(model, schemas):
    """{view, namespace, table, columns} per table the model reads: names and types, nothing the model adds.

    Datasets can be aliases of one table, as in a self-join; the view is the table, so it appears once.
    """
    datasets = list({(tuple(d.namespace), d.table): d for d in model.datasets}.values())
    names = [d.table for d in datasets]
    found = []
    for d in datasets:
        view = d.table if names.count(d.table) == 1 else "_".join([*d.namespace, d.table])
        found.append({"view": view, "namespace": list(d.namespace), "table": d.table, "columns": schemas[d.name]})
    return found


def prompt(found):
    lines = ["Tables:"]
    for table in found:
        columns = ", ".join(f"{name} {kind}" for name, kind in table["columns"].items())
        lines.append(f"{table['view']}({columns})")
    return "\n".join(lines)


@dataclass
class ControlDeps:
    services: Any
    job: dict
    views: set


def sample(rows, settings):
    shown, size = [], 0
    for row in rows[:settings.llm_rows]:
        size += len(str(row))
        if size > settings.llm_bytes:
            break
        shown.append(row)
    return shown


def create_control_agent():
    agent = Agent(name="schema_only", deps_type=ControlDeps, instructions=INSTRUCTIONS, retries=2,
                  model_settings={"temperature": 0.1}, defer_model_check=True)

    @agent.tool
    async def run_sql(ctx: RunContext[ControlDeps], sql: str) -> dict[str, Any]:
        """Run one read-only DuckDB SELECT on the tables listed in your instructions and get a sample of its rows."""
        try:
            checked = sql_guard.check(sql, ctx.deps.views)
            out = await ctx.deps.services.executor.run(
                {**ctx.deps.job, "sql": checked, "params": [], "limit": MAX_ROWS, "explainSqlErrors": True})
        except CbiError as exc:
            return {"error": str(exc), "code": exc.code}
        shown = sample(out["rows"], ctx.deps.services.settings)
        return {"columns": [c["name"] for c in out["columns"]], "rowCount": len(out["rows"]),
                "truncated": out["truncated"], "rowsShown": len(shown), "rows": shown}

    return agent
