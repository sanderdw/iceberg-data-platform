# Semantic model format

Write the model as an [Apache Ossie](https://github.com/apache/ossie/blob/main/core-spec/ossie-schema.json) model. The platform stores one model per name in a `{"version", "semantic_model": [model]}` wrapper, as below; `semantic_model.py` and the MCP tool `publish_semantic_model` also accept the bare model and wrap it on publish. Keep to the keys of the Ossie schema: it allows no others.

```json
{
  "version": "0.2.0",
  "semantic_model": [
    {
      "name": "Orders",
      "description": "Web shop orders and their customers. One row in PURCHASE per placed order, identified by order_id.",
      "ai_context": {
        "instructions": "Time: All timestamps are UTC; order_date is the UTC date of ordered_at.\nGrain: one row in PURCHASE per order, one row in CUSTOMER per customer.\nMissing values: shipped_at is NULL until the order ships; it is not a delay.\nOwner: Sales team. Refresh: nightly load at 02:00 UTC.\nClassification: CUSTOMER.email is personal data; aggregate, never list it.\n'Recent' means the 7 days up to the newest order in the data: filter PURCHASE.order_date.\nState the metric definition, denominator, units, time window and exclusions with every answer.",
        "examples": ["Which share of last month's orders was cancelled?", "Which countries order the most?"]
      },
      "datasets": [
        {
          "name": "PURCHASE",
          "source": "lakehouse.sales.orders",
          "description": "A placed web shop order.",
          "primary_key": ["order_id"],
          "fields": [
            {"name": "order_id", "description": "Order number.",
             "dimension": {"is_time": false},
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
            {"name": "customer_id", "description": "The customer who placed the order.",
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]}},
            {"name": "status", "description": "placed, shipped, cancelled or returned.",
             "dimension": {"is_time": false},
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]}},
            {"name": "order_date", "description": "UTC date the order was placed.",
             "dimension": {"is_time": true}, "datatype": "Date",
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "CAST(ordered_at AS DATE)"}]}}
          ]
        },
        {
          "name": "CUSTOMER",
          "source": "lakehouse.sales.customers",
          "description": "A registered customer.",
          "primary_key": ["customer_id"],
          "fields": [
            {"name": "customer_id", "description": "Customer number.",
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]}},
            {"name": "country", "description": "Delivery country, ISO 3166 code.",
             "dimension": {"is_time": false}, "ai_context": {"synonyms": ["market", "region"]},
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "country"}]}}
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
         "expression": {"dialects": [{"dialect": "ANSI_SQL",
           "expression": "100.0 * count(*) FILTER (WHERE PURCHASE.status = 'cancelled') / nullif(count(*), 0)"}]}}
      ]
    }
  ]
}
```

## Keys

| Level | Key | Notes |
|---|---|---|
| model | `name`, `description` | The description states what the model covers and its grain. |
| model | `ai_context.instructions` | One string, one rule per line. |
| model | `ai_context.examples` | List of the interview questions the model answers. The portal shows each key of the model's `ai_context` as a labelled row. |
| dataset | `name` | A plain identifier, usually upper case. Metrics and questions refer to it as a table: `PURCHASE.amount`. |
| dataset | `source` | `lakehouse.<namespace parts>.<table>`. The table must be in the model's namespace. |
| dataset | `primary_key` | List of field names; may be empty for an event log without a key. |
| field | `name`, `description`, `expression` | The expression is SQL over the table's columns, usually just the column name. Use derived fields for recurring needs, such as a UTC date. |
| field | `dimension` | `{"is_time": false}` for every column people group or filter by, identifiers included; `{"is_time": true}` for time columns. `{}` takes `is_time` from the `datatype`. Once a dataset marks one field, governed queries offer only the marked ones. Leave it out for measures. |
| field, metric | `datatype` | One of `String`, `Integer`, `Decimal`, `Float`, `Boolean`, `Date`, `Time`, `DateTime` (no time zone), `DateTimeTz` or `Opaque`. Needed on derived fields: a time field is grouped by day, week or month only with `Date`, `DateTime` or `DateTimeTz`, and filter values are typed from it. `draft` fills it in for plain columns. |
| field, metric | `ai_context.synonyms` | List of other words people use for it, such as `["cancel rate"]`. |
| relationship | `name`, `from`, `to`, `from_columns`, `to_columns` | From the many side (`from`, the dataset that looks up) to the one side (`to`); `to_columns` is exactly the `to` dataset's `primary_key`. Name it after its role, such as `departure_airport`. |
| metric | `name`, `description`, `expression` | An aggregate over one dataset's columns, written with its name. End the description with `Unit: …`. |

Expressions use the `ANSI_SQL` dialect and run in DuckDB, so `FILTER (WHERE …)` and `INTERVAL 7 DAY` work. Governed queries (the MCP tool `query_semantic_model` and Conversational BI) accept only scalar and aggregate expressions over the model's own columns, in metrics and in the fields they group or filter by: aggregates (`count`, `sum`, `avg`, `min`, `max`, `median`, `stddev`, `quantile_cont`, `approx_count_distinct`, `count_if`, `bool_and`, `bool_or`), `CASE`, `CAST`, `TRY_CAST`, `coalesce`, `nullif`, `greatest`, `least`, rounding and arithmetic, `extract`, `date_trunc`, `date_diff`, `date_add`, `strftime`, `current_date`, `upper`, `lower`, `length`, `trim`, `substring`, `concat`, `LIKE` and `IN` with a list. No subqueries, table functions or other functions such as `timezone()`. The portal returns a warning for every metric it would refuse when you publish.

How a governed query is built: it starts from the dataset of the requested metrics (all from one dataset), joins other datasets only along relationships in their `from` → `to` direction onto a primary key, so a join never repeats rows, and groups by the requested dimensions.

## Pitfalls

- **Dates from timestamps.** `CAST(ts AS DATE)` on a `timestamptz` column follows the session time zone. Every platform session (governed queries, `iceberg_connect.py`, notebooks, previews) runs in UTC, so it gives the UTC day. Say in the instructions that dates are UTC, for readers with other tools.
- **Relative windows.** Don't build "last 7 days" into metrics: a subquery such as `(SELECT max(ordered_at) …)` is refused by governed queries, and `now()` returns nothing for data that stopped loading. Keep metrics timeless, mark a time dimension, and state in the instructions how to window a question: a filter on that dimension, ending at the newest row in the data unless the user wants the clock.
- **Joins that repeat rows.** Governed queries only join from many to one, so a metric is never inflated. The price: a metric counts one dataset, and dimensions come from that dataset or the ones it looks up. "Orders per customer country" works with metrics on PURCHASE; "customers per order status" does not. Ask such a question as two queries, and don't build metrics that combine columns of two datasets.
- **Several paths to one dataset.** A flight has a departure and an arrival airport. Give each relationship a role name and say in the instructions which `via` means what; without `via`, an agent's query is refused as ambiguous.
- **Ratios.** Always `nullif(denominator, 0)`, and state in the description what the denominator includes.
- **Sentinels.** Treat `NULL`, `''`, `'Unknown'` and `'null'` together when the interview says they all mean unknown.
- **Names.** Model names use letters, digits, `-` and `_`. Renaming means publishing under the new name and removing the old model in the portal.
