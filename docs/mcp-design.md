# Why the MCP tools work this way

The [MCP tools](mcp.md) make choices that limit what an agent can do. Each section below names a choice, the reason for it and what it costs.

## Agents pick names; the model holds the SQL

`query_semantic_model` takes metric names, `DATASET.field` dimensions and typed filters, never SQL.
The portal builds the query from the model's own expressions:
- Filter values become parameters, never part of the SQL text.
- Metric and field expressions must pass an allow-list of scalar and aggregate functions.
- The query runs in a sandboxed DuckDB process that can read only the bound tables, in UTC.

**Why:** A metric means the same thing in every answer, whoever asks. An agent cannot read other
tables, change data, or answer from a definition the model owners did not agree.

The trade-offs:
- Functions outside the allow-list are refused, among them `timezone()` and `AT TIME ZONE`.
  Hours and dates are UTC; a model's instructions should say so.
- Relative windows such as "last 7 days" are filters at question time, not subqueries in a metric.

## Joins go from many to one only

A query starts at the dataset its metrics count (the metric's home). It joins only along declared
relationships whose `to_columns` are the target's primary key.

**Why:** Every join is then many-to-one, so it can never repeat rows and inflate a sum or a count.
The price is that one query counts one dataset. Comparing numbers from two datasets, such as seven
sector tables each with its own metrics, takes one query per dataset. The model's instructions
should say so, and a single long table with a sector column is easier to query than one table per
sector.

## `describe_semantic_model` summarises by default

The full answer lists every field and metric with its whole description and expression. For a model
the size of CBS *Regionale kerncijfers* (about 300 fields and 250 metrics), that is 150 to 300 KB,
or 50,000 to 80,000 tokens. That fills much of an agent's context before it asks anything.

The summary keeps what an agent needs to pick names:
- the model description and its AI instructions, in full
- per dataset: its fields with datatype, dimension flags and the first sentence of the description
- per metric: the dataset it counts, its unit, its expression, its synonyms and the first sentence of
  its description
- relationships, and `queryable.problems`, in full
- `queryable.dimensions` as plain `DATASET.field` names. Only an entry that needs a join path, or
  that is ambiguous and needs `via`, keeps its details.

`search` keeps the fields and metrics whose name, synonyms or description contain the text. `dataset`
keeps one dataset and the metrics that count it. Both report how many items they kept (`matched`).
`detail="full"` returns everything.

**Why summary is the default:** Agents call this tool with default arguments first. The default
should work for the largest models. An agent that needs every word asks for it.

## `list_dimension_values` finds exact spellings

Filters compare exact values, and real data has spellings an agent cannot guess:
- CBS names regions with a level suffix, such as `Utrecht (PV)` for the province.
- Municipalities with the same name get a province suffix, such as `Laren (NH.)`.

Without this tool, an agent either guesses and gets an empty answer, or abuses a metric query to list
values.

The tool's limits, and why:
- **Only fields the model marks as dimensions.** That is all a query may filter on anyway, and it
  respects what the model owners chose to expose.
- **One dataset, no joins.** Values are listed from the field's own dataset.
- **`search` is a parameter.** It is matched case-insensitively with `contains`, and never becomes
  SQL.
- **Your permissions and a query slot.** It runs as you, in the same sandbox and slots as
  `query_semantic_model`.
- **Row counts with each value.** They show at once which spelling is the common one.

## Query slots

Previews and governed queries run in separate DuckDB processes, capped at 4 GB of address space and
20 seconds of CPU for a preview, and 6 GB and 60 seconds for a query. The portal runs at most
`USER_QUERY_SLOTS` of them at once, 2 by default, for all users together.

When every slot is taken, an MCP preview or query waits up to `USER_QUERY_WAIT_SECONDS` (15 by
default) for one, and only then reports "Query slots are busy" or "Preview slots are busy".

**Why the slots exist:** A few heavy queries must not starve the gateway that serves everyone.

**Why MCP calls wait:** Agents often ask several questions at once. When MCP calls failed straight
away, four of six parallel questions failed, and agents retried in a loop or gave up.

The portal's own **Preview** button still fails at once with HTTP 429, because a person can simply
click again. Raise `USER_QUERY_SLOTS` when the host has the memory for it. Both settings are in
[configuration](configuration.md).

## Publishing checks fields against the table's columns

`publish_semantic_model` checks the model's structure, warns about metrics a governed query would
refuse, and reads each dataset's table:
- A field whose expression is a plain column name must be a column of that table. A typo blocks the
  model with "is not a column of the table".
- A table that is missing or that you cannot read does not block the model. It becomes a warning
  instead.
- A dataset without a description gets the table's comment, and a plain-column field without one
  gets the column's doc, such as those a dbt build writes. Derived fields never inherit.

**Why:** A typo in a field otherwise surfaces only later, as a failing query in someone else's chat.
A missing table is only a warning because a model may be written before its tables are loaded.

Derived fields, whose expression is more than a column name, are not executed at publish time. The
`semantic-model` skill's `check` command runs every field, metric and interview question against the
real data before it publishes.

## Units and additivity come from the model

Each metric in a query result has:
- **`unit`**, read from `Unit: …` in the metric's description. End every metric description with it,
  such as `Unit: percent.` or `Unit: 1 000 euro.`, because agents show it with the number.
- **`additive`**, which is `true` only when the whole expression is a plain `sum` or `count`. Such a
  metric may be added up across rows.

Ossie has no way to say that a metric adds up over regions but not over time, such as a population
on 1 January. The portal does not invent one. Write it in the metric's description and in the model's
instructions, such as: "Add up over regions of one level within one year; never over years."

## Not built, on purpose

- **Uploading data through MCP.** Files can be large, a URL fetched by the server opens the door to
  server-side request forgery, and inferring a schema needs decisions only the owner can make. Load
  data with [`iceberg_connect.py`](connect.md) and PyIceberg
  or DuckDB, which write with your own grants. Then describe the tables with a semantic model.
- **Time zones in governed queries.** Local time needs `timezone()` or `AT TIME ZONE`, which the
  allow-list refuses, and every session runs in UTC. A model can still offer local time as a stored
  column.
- **Semi-additive metrics.** See [units and additivity](#units-and-additivity-come-from-the-model).

[Build a semantic model](build-a-semantic-model.md#write-it-so-agents-answer-well) turns these choices into rules for model authors.
