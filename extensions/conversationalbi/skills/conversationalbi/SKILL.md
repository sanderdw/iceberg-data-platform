---
name: conversationalbi
description: Answer business questions with governed queries on the Iceberg Data Platform's semantic models through the Conversational BI MCP tools. Use when a user asks for a metric, a trend, a comparison or a KPI from their team's data or from a data product shared with their team, or asks which metrics or data they can explore.
---

# Conversational BI on the Iceberg Data Platform

The MCP server `iceberg-conversationalbi` (`http://localhost:3007/mcp`, on Kubernetes `https://cbi.<domain>/mcp`) answers questions with queries
compiled from Apache Ossie semantic models. You pick metric and field names; the platform writes
the SQL, applies the model owner's joins and definitions, and reads with a read-only token.

## Workflow

1. `list_semantic_models`: the models of your teams and those shared with your teams, in
   `semanticModels`. Each has its reference in `model`: `database`, `namespace` and `name`.
   `notEnabled` lists environments where a team administrator must call `enable_environment` first.
2. `describe_semantic_model(database, namespace, model)`, with the reference's `name` as `model`:
   datasets, fields with types, metrics with definition and unit, and for each metric the
   dimensions it can be split by, with their join path or the options when ambiguous.
3. `query_semantic_model(database, namespace, model, metrics, ...)` with metric names and
   `DATASET.field` dimensions. Add `grain` (day to year) on a date field, `via` with a relationship
   name when `describe_semantic_model` marks a dimension ambiguous, and filters on fields or
   metrics. The result names `dataAsOf`, when the metrics' data was last committed (RFC 3339, UTC),
   and `recommended`, the chart kind (or KPI or table) that fits its columns.
4. `get_result(result, offset, limit)` pages through rows of a result you asked for; `result` is the
   `resultId` of the query.

`whoami` shows your teams, where Conversational BI is enabled (`enabledEnvironments`) and the
databases your teams received a share of. `list_environments`, `enable_environment` and
`disable_environment` return an environment as `{team, environment, enabled, enabledBy, databases,
sharedDatabases}`. A refusal reads `<text> [<code>]`, such as `[ambiguous_join]` or `[fan_out]`.

## Rules

- Never compute or estimate numbers yourself: use `query_semantic_model`, and quote its rows.
- State each metric's definition and unit, the time window, the filters and the join path.
- `fan_out` means the split would double count; explain it instead of working around it.
- Follow the model's instructions about exclusions and units; they are part of its meaning.
