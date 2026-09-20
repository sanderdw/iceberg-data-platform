# Live portal reporting

The portal implements visual authoring, saved reports, dashboards and constrained
SQL. See [the user workflow and backup instructions](../README.md#reports-and-dashboards)
and [the design and delivered scope](../../docs/reporting-plan.md).

The working path is:

```text
viewer + active team/environment
  → authorize and resolve Iceberg snapshot
  → isolated DuckDB query using viewer's Polaris token and vended table credentials
  → bounded aggregate rows
  → generated dbt Charts values board
  → isolated dct render → SVG
  → recheck authorization and deliver
```

There are no dbt model builds, transformation pipelines, persistent DuckDB data
files or reporting tables. `definitions.py` owns versioned JSON models, bound builder
SQL and an explicit SQL AST allowlist. `execution.py` queries one viewer-authorized
table as `source`, preserves precise table values, and generates dbt Charts values
boards. Users cannot submit connection profiles, Jinja or arbitrary YAML.

## Finding: use native DuckDB execution and dbt Charts rendering

The separately locked environment uses Python 3.13, dbt Charts 0.8.0,
dbt-duckdb 1.10.0 and DuckDB 1.5.5. dbt Charts currently requires Python below
3.14, whereas the portal runs Python 3.14. Its image also records installed DuckDB
extension versions in `/opt/duckdb/extensions/VERSIONS`.

In this dbt Charts release, an ordinary SQL board with a named `dbt_profile`
DuckDB source resolves to the built-in `DuckDBAdapter`. That path opens its own
connection rather than using the profile's dbt-duckdb connection plugin. The
adapter probe reproduces this: a profile with an unavailable plugin still
successfully renders `SELECT 42`. This finding concerns named-source SQL routing;
it does not claim every dbt/Jinja execution path behaves identically.

The runtime therefore reuses the platform's native Iceberg connection and passes
only bounded query results to the renderer. The render subprocess receives no viewer
or platform credentials, no source SQL, and no inherited environment. The generated
`values` board is an ephemeral rendering input, not a replicated source dataset.

The public CLI is the integration boundary. SVG output works as an image, avoiding
HTML/script execution in the portal. The initial JSON render probe returns a
resolved layout/data tree; it is not assumed to be a stable browser embedding SDK.
Portal Run/Refresh controls execute the authenticated query with the current filters.
Native hover/drill-through is deferred.

## Run

Use the normal development dependencies from the repository root:

```bash
uv sync --locked --all-groups
docker compose up -d --wait polaris
docker build -f user_portal/reporting/Dockerfile -t iceberg-user-reporting:0.1.0 .
# Build all gateway, notebook and reporting images:
docker compose -f compose.users.yaml --profile images build
uv run --all-groups python -m scripts.reporting_smoke
```

The smoke test creates uniquely named teams, four users and a database. It creates
fictional Iceberg data, runs reports on an internal Docker network, and removes
its test resources in cleanup. It never starts or modifies a user's notebook.
It also runs the production job API and container runtime through saved reports,
dashboard filters, SQL, cache hits, v3 reads and actual membership revocation.
Only aggregate demo results and charts are written to `test-results/reporting/`:

- `initial.svg`, `filtered.svg`, `after-append.svg`, `iceberg-v3.svg` and other renders;
- matching JSON data and standalone dbt Charts `.yml` boards;
- `timings.json`, including query, rendering and end-to-end orchestration time.

Cached executions report zero query/render time, plus the current authorization
and delivery time. These are tiny-fixture measurements, not performance promises.
The initial run over 26,880 readings measured approximately 0.3 seconds for the
query and 3.8 seconds for a cold CLI renderer. Renderer startup dominates this
sample. The first release uses disposable workers for isolation; representative
larger-data benchmarks are still required before making latency commitments.

The independent adapter probe does not require Docker or a data service:

```bash
uv sync --locked --project user_portal/reporting --python 3.13
uv run --project user_portal/reporting python -m scripts.reporting_adapter_probe
```

## Verified behavior

- A live KPI and time-series chart over 26,880 synthetic readings.
- A category filter reruns the query and returns 6,720 selected readings.
- Appending one row changes the snapshot and returns 26,881, bypassing the old cache.
- Explicit refresh bypasses the cache even when the snapshot is unchanged.
- Two readers execute separately; a cache entry cannot cross viewer/session or active scope.
- An outsider fails both gateway authorization and an actual worker catalog read.
- Revoking membership prevents returning an already cached report.
- The Iceberg v3 fixture reports 436 remaining events after updates and deletions,
  with its variant, geometry, nanosecond timestamp and deletion-vector metadata.
- Tests cover discarded in-flight results after context, schema, permission or expiry
  changes, query parameterization and quoting, empty snapshots, exact data serialization,
  and a bounded/expiring cache.

The original proof fixture groups timestamps in **UTC**, explicitly labeled in the chart and data.
The synthetic dataset covers seven Amsterdam calendar days, so it spans eight UTC
dates. The portal supports UTC and Europe/Amsterdam, applying the selected timezone
to grouping, result metadata and cache keys. Exact API integer/decimal values are retained as strings when needed;
the separate chart projection uses floating-point numbers and may be approximate.

## Job service and operating bounds

`api.py` installs authenticated CRUD, SQL-preview and asynchronous run/poll/cancel
endpoints. `store.py` stores team/environment definitions and immutable revisions
in SQLite (schema version 1). Authors or team administrators can modify saved
definitions. A single gateway serializes authorization and definition changes;
container execution runs outside that lock. Jobs cap at four globally and two per
session. Dashboard cards execute sequentially, with one pinned snapshot per source
table across the refresh. Replaced tables and changed schemas invalidate results.

The session-scoped result cache expires after 60 seconds and caps at 16 MB. Completed
jobs expire after two minutes and cap at 16 retained jobs / 32 MB; each dashboard
result caps at 8 MB. Every cache lookup resolves current metadata and every delivery
reauthorizes. Logout/context changes clear results and cancel active workers.
`runtime.py` removes worker containers/networks on completion, failure, cancellation
or timeout; it recovers labeled orphan workers before the first run after restart.
Sessions, jobs and cache are not durable. SQLite definitions are durable on the
gateway's separate reports volume. `ReportingProof` remains only a regression fixture.

The disposable worker has a read-only root filesystem, unprivileged UID, no Linux
capabilities, no Docker socket or notebook mounts, 1 GiB container memory, 512 MiB
DuckDB memory, 128 MiB temporary storage, two CPUs, 128 processes and a 45-second
outer wall timeout per card. A query returns at most 1,000 rows, 20 columns and a
4 MB response. The visual builder applies the selected LIMIT; SQL exceeding the
output ceiling fails. Extensions are
loaded from the image; the internal network has no outbound internet route.

Multi-table SQL needs separate dependency authorization and storage secrets and is
deferred. Report parameters and SQL are visible to the active team, while data
access remains the viewer's. There is no shared result cache or author execution.
