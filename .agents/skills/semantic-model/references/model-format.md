# Semantic model format

Write the model as an Apache Ossie document. `semantic_model.py` accepts the whole document or a bare model, and wraps it for Polaris on publish.

```json
{
  "version": "0.2.0",
  "semantic_model": [
    {
      "name": "Orders",
      "description": "Web shop orders and their customers. One row in PURCHASE per placed order, identified by order_id.",
      "ai_context": {
        "instructions": "Time: All timestamps are UTC; order_date is the UTC date of ordered_at.\nGrain: one row in PURCHASE per order, one row in CUSTOMER per customer.\nMissing values: shipped_at is NULL until the order ships; it is not a delay.\nOwner: Sales team. Refresh: nightly load at 02:00 UTC.\nClassification: CUSTOMER.email is personal data; aggregate, never list it.\n'Recent' means the 7 days up to the newest order in the data.\nState the metric definition, denominator, units, time window and exclusions with every answer."
      },
      "datasets": [
        {
          "name": "PURCHASE",
          "source": "lakehouse.sales.orders",
          "description": "A placed web shop order.",
          "primary_key": ["order_id"],
          "fields": [
            {"name": "order_id", "description": "Order number.",
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
            {"name": "status", "description": "placed, shipped, cancelled or returned.",
             "dimension": {"is_time": false},
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]}},
            {"name": "order_date", "description": "UTC date the order was placed.",
             "dimension": {"is_time": true},
             "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "CAST(ordered_at AS DATE)"}]}}
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
| model | `ai_context.instructions` | One string, one rule per line. The portal shows each key of `ai_context` as a labelled row, so you may add others, such as `synonyms`. |
| dataset | `name` | A plain identifier, usually upper case. Metrics and questions refer to it as a table: `PURCHASE.amount`. |
| dataset | `source` | `lakehouse.<namespace parts>.<table>`. The table must be in the model's namespace. |
| dataset | `primary_key` | List of field names; may be empty for an event log without a key. |
| field | `name`, `description`, `expression` | The expression is SQL over the table's columns, usually just the column name. Use derived fields for recurring needs, such as a UTC date. |
| field | `dimension` | `{"is_time": false}` for columns to group or filter by, `{"is_time": true}` for time columns. Leave it out for measures and identifiers. |
| relationship | `name`, `from`, `to`, `from_columns`, `to_columns` | Many-to-one, from the detail dataset to the one it looks up. |
| metric | `name`, `description`, `expression` | An aggregate over dataset names. End the description with `Unit: …`. |

Expressions use the `ANSI_SQL` dialect and run in DuckDB, so `FILTER (WHERE …)` and `INTERVAL 7 DAY` work. Governed queries (the MCP tool `query_semantic_model` and Conversational BI) accept only scalar and aggregate expressions over the model's own columns: no subqueries, table functions or unlisted functions such as `timezone()`. The portal returns a warning for every metric it would refuse when you publish.

## Pitfalls

- **Dates from timestamps.** `CAST(ts AS DATE)` on a `timestamptz` column follows the session time zone. Every platform session (governed queries, `iceberg_connect.py`, notebooks, previews) runs in UTC, so it gives the UTC day. Say in the instructions that dates are UTC, for readers with other tools.
- **Relative windows.** Don't build "last 7 days" into metrics: a subquery such as `(SELECT max(ordered_at) …)` is refused by governed queries, and `now()` returns nothing for data that stopped loading. Keep metrics timeless, mark a time dimension, and state in the instructions how to window a question: a filter on that dimension, ending at the newest row in the data unless the user wants the clock.
- **Distinct counts across joins.** A join that multiplies rows inflates `count(*)` and `sum`; say in the instructions which joins are safe for which metrics.
- **Ratios.** Always `nullif(denominator, 0)`, and state in the description what the denominator includes.
- **Sentinels.** Treat `NULL`, `''`, `'Unknown'` and `'null'` together when the interview says they all mean unknown.
- **Names.** Model names use letters, digits, `-` and `_`. Renaming means publishing under the new name and removing the old model in the portal.
