# Table-property conventions v1

Any producer (a dbt run, a notebook, a Spark job) may set these Iceberg table properties. The core
user portal renders them generically in the catalog browser, and any Iceberg engine can read them.
They are **display data written by anyone with write access**: the core never uses them for
authorization, caps their size and escapes them when it renders them.

| Property | Value |
| --- | --- |
| `comment` | Table description (Spark and Trino use the same key) |
| `iceberg-data-platform.producer` | Producer id, such as `dbt` |
| `iceberg-data-platform.producer.version` | Producer version, such as `dbt-oss 2.0.5` |
| `iceberg-data-platform.producer.url` | Deep link to the producer's page for this table. Rendered only when its origin is a registered extension origin |
| `iceberg-data-platform.producer.run-id` | Id of the run that last wrote the table |
| `iceberg-data-platform.producer.run-at` | ISO-8601 UTC time of that run |
| `iceberg-data-platform.producer.source-revision` | Source revision, such as a Git commit |
| `iceberg-data-platform.producer.node` | The producer's own id for the table, such as `model.sales.orders` |
| `iceberg-data-platform.lineage.inputs` | JSON list of up to 50 upstream tables: `[{"database": "db-…", "namespace": ["…"], "name": "…"}]` |
| `iceberg-data-platform.lineage.inputs-truncated` | `true` when there were more than 50 inputs |
| `iceberg-data-platform.quality.status` | `pass`, `warn` or `fail` for the tests of the last run |
| `iceberg-data-platform.quality.checked-at` | ISO-8601 UTC time of those tests |
| `iceberg-data-platform.quality.summary` | Short text, such as `4 passed, 1 warning` (at most 200 characters) |

Column descriptions use the Iceberg schema field `doc`, which the portal already shows.

Property names must not contain `token`, `secret`, `password`, `credential` or `key` as a word;
the portal hides such properties. A producer writes metadata only, never data, with these keys,
and keeps the table's identity (UUID) across runs so that data shares and properties survive.
