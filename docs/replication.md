# Replication (OLake, beta)

Keep operational databases in step with a team's Apache Iceberg databases. Each run reads the changes since the last one with [OLake](https://github.com/datazip-inc/olake-go): inserts, updates and deletes from PostgreSQL's write-ahead log (change data capture), or the rows whose cursor column grew. It writes them as the team's service account and stops. Run it every few minutes on a schedule. People use the replications page; AI agents and scripts use the same capabilities through MCP and a REST API.

**0.0.1 is the first, beta release.** It supports PostgreSQL only, and OLake has open issues that affect some tables; see [known issues](#known-issues) before you rely on it.

| | |
| --- | --- |
| Sources | PostgreSQL with logical replication (tested with PostgreSQL 17). MySQL, MongoDB, SQL Server and Oracle are planned |
| Page | `http://localhost:3014` (replications, source checks, tables, runs, logs, schedules), linked from the user portal as **Replication (OLake)** |
| API | `http://localhost:3014/api/v1` |
| MCP | `http://localhost:3014/mcp` (Keycloak sign-in through the `ext-replication-mcp` client) |

## Replication or Ingestion

| Use | When |
| --- | --- |
| [Ingestion (dlt)](ingestion.md) | REST APIs; CSV, JSONL or Parquet files; a one-time or occasional load of a database table |
| **Replication (OLake)** | Keeping an operational database in step: updates and deletes as they happen, large tables, runs every few minutes |

Ingestion's database source sees no deletes and rewrites rows in batches, so use it to load a table once or now and then. Replication follows the database's own change log.

## Install

The installer asks whether to add Replication. To install without questions:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | ICEBERG_EXTENSIONS=replication sh
```

```powershell
$env:ICEBERG_EXTENSIONS = 'replication'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

A team administrator enables it once per environment with **Enable** on the page, creates replications and enters the source's password. Passwords are write-only: no page, API or MCP response returns them. Writers check sources and start runs; a schedule starts them too.

Operational databases usually live on a private network. Runs reach only public addresses until you list your database networks in `replication/.env`, for example `REP_ALLOWED_SOURCE_NETWORKS=10.20.0.0/16,192.168.5.0/24`, and restart the extension with `docker compose -f replication/compose.yaml up -d --wait`. Loopback and link-local addresses stay refused. On Kubernetes, install the chart with `runs.allowedSourceNetworks`.

`replication/.env` in the installation directory holds `REP_SECRET_KEY`, which decrypts the stored passwords. Rerunning the installer keeps it, with the extension, its replications and their runs. On Kubernetes it is in the Secret `iceberg-replication`; back it up, because the stored passwords can't be read without it.

On Kubernetes, install its chart next to the platform's; see [Install on Kubernetes](install-kubernetes.md#add-extensions).

## Prepare the source

The database owner prepares PostgreSQL once:

1. Set `wal_level = logical` (a server restart; on Amazon RDS and Aurora the parameter `rds.logical_replication = 1`).
2. Give the replication's user the `REPLICATION` attribute (`rds_replication` on RDS) and `SELECT` on the tables.
3. Create a publication for the tables and a logical replication slot with the `pgoutput` plugin.
4. Give a table without a primary key, and every table you partition, `REPLICA IDENTITY FULL`. Otherwise a delete carries only the key, and a deleted row stays visible in DuckDB.

The replication's page shows this as SQL for the tables you choose (**Set up the source**), and **Check source** tells what is missing.

A replication slot keeps the source's change log until a run has read it. If runs stop, the source's disk fills up: delete the replication and have the owner drop its slot, with the SQL the page gives you.

## Use it

1. **New replication**: the source's host, port, database, user and TLS mode, the slot and publication, and the database and namespace to write into, such as `raw_crm`.
2. Set the **password**.
3. **Check source**: OLake's checks (logical WAL, replication privilege, slot, publication and the advised `wal_sender_timeout`) and the tables it can replicate, with their primary keys.
4. **Choose tables**. Per table:

    | Sync | What a run does |
    | --- | --- |
    | Changes (CDC) | Follows inserts, updates and deletes from the change log. Needs the slot and publication, and the table in the publication |
    | Incremental | Reads the rows whose cursor column (such as `updated_at`) grew since the last run. Misses deletes |
    | Full copy | Copies the whole table on every run |

    Optionally an Iceberg table name and a partition, such as `/{created_at,month}`. A table without a primary key is appended to.
5. **Run now**, then a schedule such as every 15 minutes (`*/15 * * * *`, UTC).

The first run copies the existing rows (**First run**: *Copy rows, then changes*), or only follows changes from then on (*Only new changes*). A first copy that doesn't fit in one run stops at the time limit with status **partial**; the next run continues where it stopped.

**Copy everything again** starts a run without the saved change log position. Tables with a primary key keep one row per key; append-only tables get their rows again.

## What the tables look like

- Updates and deletes are written as **positional deletes** (Iceberg v2, the default) or **deletion vectors** (Iceberg v3, smaller). DuckDB, PyIceberg and Polars read both, so notebooks, Conversational BI and data shares see the current rows. Equality deletes, OLake's own default, are not offered: PyIceberg and Polars can't read tables that have them.
- A row deleted in the source stays in the table with `_op_type = 'd'`. Filter on `_op_type <> 'd'` for the current rows, for example in a dbt staging model or a semantic model.
- OLake adds `_olake_id`, `_olake_timestamp`, `_op_type` (`r` copied, `c` inserted, `u` updated, `d` deleted), `_cdc_lsn` and `_cdc_timestamp`.
- The catalog shows each table as **Produced by olake**, with a link back to the replication and the change log position it was read up to.
- Each sync writes one row to the table `test_olake.test_olake` in the database: OLake's check that it can write there.
- Every run with updates or deletes adds delete files. Let the [Maintenance](maintenance.md) extension compact them.

## Connect an AI agent

```bash
claude mcp add --transport http --client-id ext-replication-mcp --callback-port 3010 iceberg-replication http://localhost:3014/mcp
```

The `replication` [agent skill](agent-skills.md) in the installation folder guides an agent from a source check to a first sync. Register the [user portal's MCP server](ai-agents.md) too, to read the tables.

### MCP tools

What each role may do is in [roles and permissions](reference/roles.md#extensions).

| Tool | What it does |
| --- | --- |
| `whoami` | Your teams with your role in each, their databases per environment, where Replication is enabled, and this extension. |
| `list_environments` | Where Replication is enabled for a team, and the databases of each environment. |
| `enable_environment` / `disable_environment` | Enables Replication for a team environment, where runs act as the team's service account, or disables it (team administrators). |
| `list_templates` | A complete replication per kind (change data capture, incremental) to adapt, with the setup SQL. |
| `list_replications` / `get_replication` | Replications with their schedule and last run; one replication with its tables, whether the password is set (never its value), its last source check, setup SQL, warnings and recent runs. |
| `validate_replication` | Checks a definition without saving it: allowed keys, addresses, tables, options and the setup SQL. |
| `create_replication` / `update_replication` | Creates or changes a replication (team administrators). A new host or user removes the password; a new source, slot, database or namespace copies everything again. |
| `delete_replication` | Deletes a replication with its runs, schedule and password (team administrators). Its tables stay. Returns the SQL to drop the replication slot. `confirm_name` must equal its name. |
| `set_replication_secrets` | Sets, replaces or removes the password (team administrators). Write-only. |
| `check_source` | Connects with the stored password (writers): OLake's checks and the tables it can replicate. |
| `run_replication` | Runs now (writers). `full_refresh` copies everything again; `wait` blocks up to 600 seconds. |
| `list_runs` / `get_run` / `get_run_logs` | Runs and source checks; one run's status, rows written and replaced per table, and the change log position; its log, with secrets masked. |
| `cancel_run` | Stops a queued or running run (writers). Committed changes stay. |
| `set_schedule` / `delete_schedule` | Runs on a five-field cron schedule in UTC, at most every 5 minutes, or removes it (team administrators). |

## Isolation and limits

- Each run is its own container from the pinned runner image, built on OLake's own image (on Kubernetes a Job in `<namespace>-replication-runs`), with a read-only file system, that writes as the team's service account. It reaches the catalog, storage and its source, but not the platform's other services. OLake's telemetry is off.
- The source's host name is resolved and checked before every run, and the run gets that address pinned: it never resolves the name itself.
- A run ends before its catalog token expires: about 42 minutes with the platform's default `POLARIS_TOKEN_LIFETIME` of one hour. A first copy continues in the next run; raise the lifetime in `.env`, for example to 7200, to copy large tables in fewer runs (on Kubernetes `polaris.tokenLifetime`); see [configuration](configuration.md).
- A run has 4 GB of memory (`RUN_MEMORY` in `replication/.env`, `runs.memory` on Kubernetes). A very large burst of changes, such as a bulk load of millions of rows into a replicated table, can need more; the run then fails with "probably out of memory", and every next run fails the same way until you raise the limit.
- At most 4 runs at once, 2 per team, and one per replication. A schedule skips its time while the replication is still running; the next run reads everything since.

## Known issues

These come from OLake itself. With the standard setup they don't affect you; this is what to watch for.

| Situation | What you see | What to do |
| --- | --- | --- |
| A partitioned table whose source table lacks `REPLICA IDENTITY FULL` | Rows deleted in the source stay visible in DuckDB (notebooks, Conversational BI, data shares), so counts and totals are too high. PyIceberg and Polars show the right rows, so tools disagree | Run the setup SQL the page shows: it sets `REPLICA IDENTITY FULL` for partitioned tables. Or don't partition. Deletes from before the fix stay wrong; copy everything again to repair them ([OLake #1257](https://github.com/datazip-inc/olake-go/issues/1257)) |
| A very large change in one go, such as millions of rows loaded or updated in one transaction | Runs fail with "probably out of memory", every run again; the tables stop being updated, and the source keeps its change log, so its disk fills up | A platform administrator raises `RUN_MEMORY` (`runs.memory` on Kubernetes); the next run catches up ([OLake #801](https://github.com/datazip-inc/olake-go/issues/801)) |
| A row is deleted and later added again with the same primary key | Two rows for that key: the deleted version and the new one | Filter on `_op_type <> 'd'`, as for every replicated table ([OLake #1286](https://github.com/datazip-inc/olake-go/issues/1286), fix in progress) |
| A primary key value changes in the source, such as customer 100 renumbered to 200 | Both rows stay; the old one is not marked deleted, so the filter doesn't help | Avoid changing keys: delete and insert instead. Copy everything again to repair a table ([OLake #1363](https://github.com/datazip-inc/olake-go/issues/1363)) |

How it is built: [Replication internals](develop/replication.md).
