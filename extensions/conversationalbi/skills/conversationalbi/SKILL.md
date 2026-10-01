---
name: conversationalbi
description: Answer business questions with governed queries on the Iceberg Data Platform's semantic models through the Conversational BI MCP tools. Use when a user asks for a metric, a trend, a comparison or a KPI from their team's data or from a data product shared with their team, or asks which metrics or data they can explore.
---

# Conversational BI on the Iceberg Data Platform

The MCP server `conversationalbi` (`http://localhost:3007/mcp`) answers questions with queries
compiled from Apache Ossie semantic models. You pick metric and field names; the platform writes
the SQL, applies the model owner's joins and definitions, and reads with a read-only token.

## Workflow

1. `list_models`: the models of your teams and those shared with your teams. `notEnabled` lists
   environments where a team administrator must call `enable_environment` first.
2. `describe_model`: datasets, fields with types, metrics with definition and unit, and for each
   metric the dimensions it can be split by, with their join path or the options when ambiguous.
3. `query` with metric names and `DATASET.field` dimensions. Add `grain` (day to year) on a date
   field, `via` with a relationship name when describe_model marks a dimension ambiguous, and
   filters on fields or metrics. The result names `dataAsOf`, when the metrics' data was last
   committed, and `recommended`, the chart kind (or KPI or table) that fits its columns.
4. `get_result` pages through rows of a result you asked for.

## Rules

- Never compute or estimate numbers yourself: use `query`, and quote its rows.
- State each metric's definition and unit, the time window, the filters and the join path.
- `fan_out` means the split would double count; explain it instead of working around it.
- Follow the model's instructions about exclusions and units; they are part of its meaning.
