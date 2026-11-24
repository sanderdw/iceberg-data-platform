# Maintenance (beta)

Keep your tables in shape: expire old snapshots, compact small data files, clean up delete files and manifests, and remove orphan files in a team's Apache Iceberg databases, on a schedule or on demand. People use the table health page; AI agents and scripts use the same capabilities through MCP and a REST API.

!!! warning "Beta"
    Maintenance 0.0.1 is the first version. Its runner uses an unreleased commit of [Apache iceberg-go](https://github.com/apache/iceberg-go). Try it on development databases first, and dry-run every policy.

| | |
| --- | --- |
| Operations | Expire snapshots, compact data files, remove dangling delete files, rewrite manifests, remove orphan files |
| Page | `http://localhost:3013` (table health, policies, runs), listed under **Apps** in the user portal as **Maintenance** |
| API | `http://localhost:3013/api/v1` |
| MCP | `http://localhost:3013/mcp` (Keycloak sign-in through the `ext-maintenance-mcp` client) |

## Why tables need maintenance

Every write to an Iceberg table adds a snapshot, and usually a few small files. A pipeline that loads every 15 minutes adds almost a hundred of each a day. Old snapshots keep their files, for time travel, so storage keeps growing. Many small files make queries slow, because each one is opened and read separately. Deletes and updates add delete files that every query has to apply. Maintenance cleans this up, table by table, as your team's service account.

## Install

The installer asks whether to add Maintenance. To install without questions:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | ICEBERG_EXTENSIONS=maintenance sh
```

```powershell
$env:ICEBERG_EXTENSIONS = 'maintenance'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

A team administrator then enables it once per environment with **Enable** on the page. Rerunning the installer keeps the extension, its policies and its runs.

On Kubernetes, install its chart next to the platform's; see [Install on Kubernetes](install-kubernetes.md#add-extensions). To run it from source, see [Maintenance internals](develop/maintenance.md#run-from-source).

## Table health

The page lists every table of your teams' databases with what the last run found: snapshots and the age of the oldest, data files and how many are small, delete files and manifests. A table needs attention when the policy would compact files or expire snapshots, or when it has 5 or more delete files or too many manifests. **Check health** reads every table of a database and changes nothing; it uses a read-only token.

A table's page shows its file sizes as a histogram, its snapshots per day, its branches and tags, the effective policy and the actions of its last run.

## Policies

A team administrator sets one policy per database, with an optional schedule (cron in UTC, at most once an hour). Without a policy, health checks and dry runs use the defaults, and nothing is maintained. The defaults follow Apache Iceberg and Amazon S3 Tables:

| Operation | Default | What it does |
| --- | --- | --- |
| Expire snapshots | On: older than 5 days, keep the last 1 | Removes old snapshots. Time travel to them stops working |
| Compact data files | On: 128 MB files, at least 5 input files, 2 GB per table and run | Rewrites files smaller than 75% of the target, and files where deletes hide 30% or more of the rows, into fewer files per partition. Sorted when the table has a sort order. Applies the deletes it rewrites |
| Remove dangling deletes | On | Drops delete files that no data file needs any more. Metadata only |
| Rewrite manifests | On: above 100 manifests | Merges small manifests so planning a query reads less metadata |
| Remove orphan files | Off: older than 3 days | Deletes files under a table's location that no snapshot references, never younger than a day |

Tables can be excluded: maintenance reads their health but never changes them.

**Dry run** plans the saved policy, or the defaults, and shows per table what it would do. It changes nothing.

## What a run never does

- Expire a branch or tag head, or ignore a branch's or tag's own retention. They stay until someone removes the branch or tag in the [user portal](user-portal.md).
- Ignore the table properties `history.expire.max-snapshot-age-ms` and `history.expire.min-snapshots-to-keep`: they override the policy, as Iceberg defines.
- Delete files of a table with `gc.enabled=false`: it keeps its history.
- Delete the files of a snapshot it just expired. They become orphans, and orphan cleanup deletes them in a later run. The time between runs is your grace period.
- Delete an orphan file younger than a day, so writes in progress are safe.
- Change a row. Compaction and manifest rewrites commit `replace` snapshots; compaction stamps them with the run in `iceberg-data-platform.maintenance.run-id`.

A table whose commit conflicts with a writer, such as an [Ingestion](ingestion.md), [Replication](replication.md) or [dbt](dbt.md) run, fails on its own; the others continue and the next run tries again.

## Connect an AI agent

```bash
claude mcp add --transport http --client-id ext-maintenance-mcp --callback-port 3010 iceberg-maintenance http://localhost:3013/mcp
```

The `maintenance` [agent skill](agent-skills.md) in `extensions/maintenance/skills` guides an agent from table health to a dry-run policy.

| Tool | What it does |
| --- | --- |
| `whoami` | Your teams with your role in each, their databases per environment, where Maintenance is enabled, and the policy defaults. |
| `list_environments` / `enable_environment` / `disable_environment` | Where Maintenance is enabled for a team; enables or disables it for an environment (team administrators). |
| `list_databases` | Your teams' databases with their policy, schedule, last run and tables that need attention. |
| `list_tables` / `get_table` | Table health from the last runs, optionally only tables that need attention; one table with its last actions and recent runs. |
| `get_policy` / `set_policy` / `delete_policy` | A database's policy and schedule, or the defaults; creates or changes it (team administrators), giving only what changes; removes it. |
| `run_maintenance` | Runs on a database now (writers): `inspect`, `dry-run` or `maintain`, optionally for some tables. `wait` blocks up to 600 seconds. |
| `list_runs` / `get_run` / `get_run_logs` / `cancel_run` | A database's runs; one run with each table's actions; its log; stops a run (writers). |

What each role may do is in [roles and permissions](reference/roles.md#extensions).

## Isolation and limits

- Each run is a container started by the extension's launcher, the only part with the Docker socket: read-only, no capabilities, memory and CPU limits (`RUN_MEMORY`, `RUN_CPUS`), and a private network that holds only the platform's catalog and storage. Runs never reach the internet. On Kubernetes each run is a Job in a namespace of its own, whose network policy allows only the catalog, storage and the launcher.
- A run acts as the team's service account for the environment, with a read token for health checks and dry runs and a write token for maintenance. It ends 15 minutes before that token expires (`POLARIS_TOKEN_LIFETIME`, one hour by default).
- One database has at most one run at a time; `MNT_MAX_CONCURRENT_RUNS` (2) limits runs across teams.
- Compaction holds about one file group in memory. Raise `RUN_MEMORY` with larger target file sizes.
- Not in this version: record expiration (row TTL) and table statistics. See the extension's [decisions](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/maintenance/DECISIONS.md).
