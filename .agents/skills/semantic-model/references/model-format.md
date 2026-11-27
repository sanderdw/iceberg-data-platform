# Semantic model format

Write the model as an [Apache Ossie 0.3.0.dev0](https://github.com/apache/ossie/blob/main/core-spec/spec.md) document: one model per document, at the root, as below. `semantic_model.py` and the MCP tool `publish_semantic_model` fill in `version` when it is missing and refuse other versions and the earlier shape with a `semantic_model` list. Keep to the keys of the [Ossie schema](https://github.com/apache/ossie/blob/main/core-spec/ossie-schema.json): it allows no others.

```json
{
  "version": "0.3.0.dev0",
  "name": "Orders",
  "description": "Web shop orders and their customers. One row in PURCHASE per placed order, identified by order_id.",
  "ai_context": {
    "instructions": "Time: All timestamps are UTC; order_date is the UTC date of ordered_at.\nGrain: one row in PURCHASE per order, one row in CUSTOMER per customer.\nMissing values: shipped_at is NULL until the order ships; it is not a delay.\nOwner: Sales team. Refresh: nightly load at 02:00 UTC.\nClassification: CUSTOMER.email is personal data; aggregate, never list it.\n'Recent' means the 7 days up to the newest order in the data: filter PURCHASE.order_date.\nState the metric definition, denominator, units, time window and exclusions with every answer.",
    "examples": ["Which share of last month's orders was cancelled?", "Which countries order the most?"]
  },
  "datasets": [
    {
      "name": "PURCHASE",
      "source": "sales.orders",
      "description": "A placed web shop order.",
      "primary_key": ["order_id"],
      "fields": [
        {"name": "order_id", "description": "Order number.", "dimension": {"is_time": false},
         "expression": {"sql": "order_id"}},
        {"name": "customer_id", "description": "The customer who placed the order.",
         "expression": {"sql": "customer_id"}},
        {"name": "status", "description": "placed, shipped, cancelled or returned.", "dimension": {"is_time": false},
         "expression": {"sql": "status"}},
        {"name": "order_date", "description": "UTC date the order was placed.",
         "dimension": {"is_time": true}, "datatype": "Date",
         "expression": {"sql": "CAST(ordered_at AS DATE)"}}
      ],
      "metrics": [
        {"name": "orders", "description": "Placed orders. Unit: orders.", "datatype": "Integer",
         "expression": {"sql": "count(*)"}}
      ]
    },
    {
      "name": "CUSTOMER",
      "source": "sales.customers",
      "description": "A registered customer.",
      "primary_key": ["customer_id"],
      "fields": [
        {"name": "customer_id", "description": "Customer number.", "expression": {"sql": "customer_id"}},
        {"name": "country", "description": "Delivery country, ISO 3166 code.",
         "dimension": {"is_time": false}, "ai_context": {"synonyms": ["market", "region"]},
         "expression": {"sql": "country"}}
      ]
    }
  ],
  "relationships": [
    {"name": "order_customer", "from": "PURCHASE", "to": "CUSTOMER",
     "from_columns": ["customer_id"], "to_columns": ["customer_id"]}
  ],
  "metrics": [
    {"name": "cancellation_pct",
     "description": "Cancelled orders divided by all placed orders, test orders excluded. Unit: percent.",
     "datatype": "Decimal", "ai_context": {"synonyms": ["cancel rate"]},
     "expression": {"sql": "100.0 * count(*) FILTER (WHERE PURCHASE.status = 'cancelled') / nullif(AGG(PURCHASE.orders), 0)"}}
  ]
}
```

## What names refer to

Ossie has three levels of names, and each place in the model reads one of them:

| Where | Reads |
|---|---|
| A field's `expression` | Columns of its dataset's table. A field never reads another field. |
| `primary_key`, `unique_keys`, `from_columns`, `to_columns` | Fields of the dataset, by name, not columns. |
| A dataset's own metric (`datasets[].metrics`) | That dataset's fields by plain name, such as `sum(net_amount)`, and its table's columns as `source.column`. |
| A model's metric (the root `metrics`) | Fields as `DATASET.field`, and a dataset's own metric as `AGG(DATASET.metric)`. |

So `SUM(PURCHASE.ordered_at)` in a model's metric is an error when `ordered_at` is a column but not a field. Names are compared without regard to case; in the portable `sql`, a quoted name such as `"order_id"` must match exactly, upper-cased: write names unquoted. Queries name a dataset's own metric `DATASET.metric`, such as `PURCHASE.orders`, and a model's metric by its name.

Use a dataset's own metric for a measure of one dataset, and a model's metric for one that builds on others. Governed queries run a metric that reads one dataset, its own fields or `AGG` of its own metrics; a model's metric over fields of two datasets is valid Ossie, but they refuse it.

## Keys

| Level | Key | Notes |
|---|---|---|
| model | `version` | `0.3.0.dev0`. |
| model | `name`, `description` | The description states what the model covers and its grain. |
| model | `ai_context.instructions` | One string, one rule per line. |
| model | `ai_context.examples` | List of the interview questions the model answers. The portal shows each key of the model's `ai_context` as a labelled row. |
| dataset | `name` | A plain identifier, usually upper case. Model metrics and questions refer to it as a table: `PURCHASE.amount`. |
| dataset | `source` | `<namespace parts>.<table>`, such as `sales.orders`, in the model's database: Polaris refuses to store a model whose source is not an existing table or view. Governed queries read tables. |
| dataset | `primary_key` | List of field names, each once; may be left out for an event log without a key. |
| dataset | `unique_keys` | Other keys, as lists of field names, such as `[["email"]]`. A relationship may end on one. |
| dataset | `metrics` | The dataset's own metrics. Their names share one namespace with the dataset's fields. |
| field | `name`, `description`, `expression` | `{"sql": "..."}`: SQL over the table's columns, usually just the column name. Use derived fields for recurring needs, such as a UTC date. |
| field | `dimension` | `{"is_time": false}` for every field people group or filter by, identifiers included; `{"is_time": true}` for time fields. `{}` takes `is_time` from the `datatype`. Once a dataset marks one field, governed queries offer only the marked ones. Leave it out for measures. |
| field, metric | `datatype` | One of `String`, `Integer`, `Decimal`, `Float`, `Boolean`, `Date`, `Time`, `DateTime` (no time zone), `DateTimeTz` or `Opaque`. Needed on derived fields: a time field is grouped by day, week or month only with `Date`, `DateTime` or `DateTimeTz`, and filter values are typed from it. `draft` fills it in for plain columns. |
| field, metric | `ai_context.synonyms` | List of other words people use for it, such as `["cancel rate"]`. Synonyms and examples are text. |
| relationship | `name`, `from`, `to`, `from_columns`, `to_columns` | From the many side (`from`, the dataset that looks up) to the one side (`to`); `to_columns` is exactly the `to` dataset's primary key or one of its unique keys. Name it after its role, such as `departure_airport`. |
| metric | `name`, `description`, `expression` | An aggregate. End the description with `Unit: …`. |
| any | `nonportable_extensions` | `[{"vendor_name": "...", "data": {...}}]`: a tool's own settings, kept as written. Nothing on the platform reads them. |

## Expressions

`expression.sql` is Ossie's portable SQL (`OSSIE_SQL_2026`): `/` is true division (`5 / 2` is `2.5`) and `ROUND` breaks ties away from zero. Optional `dialects`, such as `[{"dialect": "DUCKDB", "expression": "..."}]`, hold the same expression for one engine. The platform runs DuckDB and picks a `DUCKDB` expression first, then `sql`, then `ANSI_SQL`. DuckDB reads the portable SQL as it is, so `FILTER (WHERE …)` and `INTERVAL 7 DAY` work.

Governed queries (the MCP tool `query_semantic_model` and Conversational BI) accept only scalar and aggregate expressions over the model's own fields, in metrics and in the fields they group or filter by: aggregates (`count`, `sum`, `avg`, `min`, `max`, `median`, `stddev`, `variance`, `quantile_cont`, `approx_count_distinct`, `count_if`, `bool_and`, `bool_or`), `AGG(DATASET.metric)`, `CASE`, `CAST`, `TRY_CAST`, `coalesce`, `nullif`, `greatest`, `least`, rounding, `trunc` and arithmetic, `extract`, `year`, `quarter`, `month`, `day`, `hour`, `date_trunc`, `date_diff`, `date_add`, `strftime`, `current_date`, `current_timestamp`, `upper`, `lower`, `length`, `trim`, `substring`, `left`, `right`, `replace`, `split_part`, `position`, `contains`, `starts_with`, `ends_with`, `concat`, `LIKE`, `IS [NOT] DISTINCT FROM` and `IN` with a list. No subqueries, window functions, table functions or other functions such as `timezone()`. `AGG(source.measure)` evaluates a measure of a measure relation; an Iceberg table has none, so governed queries refuse it. The portal returns a warning for every metric it would refuse when you publish.

## Pitfalls

- **Dates from timestamps.** `CAST(ts AS DATE)` on a `timestamptz` column follows the session time zone. Every platform session (governed queries, `iceberg_connect.py`, notebooks, previews) runs in UTC, so it gives the UTC day. Say in the instructions that dates are UTC, for readers with other tools.
- **Relative windows.** Don't build "last 7 days" into metrics: a subquery such as `(SELECT max(ordered_at) …)` is refused by governed queries, and `now()` returns nothing for data that stopped loading. Keep metrics timeless, mark a time dimension, and state in the instructions how to window a question: a filter on that dimension, ending at the newest row in the data unless the user wants the clock.
- **Columns that are not fields.** A metric or key can only use what a field exposes. Add a field for every column a metric, key or relationship needs, or read the column in a dataset's own metric as `source.column`.
- **Joins that repeat rows.** A governed query starts from the dataset of its metrics and joins only from many to one, along relationships onto a whole key, so a metric is never inflated. The price: a metric counts one dataset, and dimensions come from that dataset or the ones it looks up. "Orders per customer country" works with metrics on PURCHASE; "customers per order status" does not. Ask such a question as two queries, and don't build metrics that combine fields of two datasets.
- **Several paths to one dataset.** A flight has a departure and an arrival airport. Give each relationship a role name and say in the instructions which `via` means what; without `via`, an agent's query is refused as ambiguous.
- **Ratios.** Always `nullif(denominator, 0)`, and state in the description what the denominator includes.
- **Sentinels.** Treat `NULL`, `''`, `'Unknown'` and `'null'` together when the interview says they all mean unknown.
- **Names.** Model names use letters, digits, `-` and `_`. Renaming means publishing under the new name and removing the old model with the MCP tool `delete_semantic_model` (`model` and `confirm_name` are both the old name), after the user agrees.
