# Maintenance (beta)

Keep your tables in shape: expire old snapshots, compact small data files, clean up delete files and manifests, and remove orphan files in a team's Apache Iceberg databases, on a schedule or on demand. The rules are [Apache Polaris policies](https://polaris.apache.org/), so the catalog holds them and every engine sees them; Maintenance runs them. People use the table health page; AI agents and scripts use the same capabilities through MCP and a REST API.

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

A team administrator enables Maintenance once per environment: under **Settings** › **Environments** (Settings is in the header, and Environments is its first option), through an AI agent (`enable_environment`) or with `POST /api/v1/teams/{team}/environments/{environment}`. Environments shows who enabled each environment, and **Disable** revokes the team's service account there again. Maintenance reads and writes policies as the team's service account for that environment, so policies also need it enabled. Rerunning the installer keeps the extension, its runs and its schedules; the policies are in Polaris.

On Kubernetes, install its chart next to the platform's; see [Install on Kubernetes](install-kubernetes.md#add-extensions). To run it from source, see [Maintenance internals](develop/maintenance.md#run-from-source).

## Table health

The page lists every table of your teams' databases with what the last run found: snapshots and the age of the oldest, data files and how many are small, delete files and manifests. A table needs attention when the policy would compact files or expire snapshots, or when it has 5 or more delete files or too many manifests. **Check health** reads every table of a database and changes nothing; it uses a read-only token.

A table's page shows its file sizes as a histogram, its snapshots per day, its branches and tags, the policies that applied at its last run and where each is attached, and the actions of its last run.

## Policies

A database's policy is four Apache Polaris policies, one per type, attached to the database's catalog. A team administrator sets them on the database's **Policy** page, or with `update_policy`. Saving creates the missing ones from the defaults, which follow Apache Iceberg and Amazon S3 Tables:

| Polaris type | Operation | Default | What it does |
| --- | --- | --- | --- |
| `system.snapshot-expiry` | Expire snapshots | On: older than 5 days, keep the last 1 | Removes old snapshots. Time travel to them stops working |
| `system.data-compaction` | Compact data files | On: 128 MB files, at least 5 input files, 2 GB per table and run | Rewrites files smaller than 75% of the target, and files where deletes hide 30% or more of the rows, into fewer files per partition. Sorted when the table has a sort order. Applies the deletes it rewrites |
| `system.data-compaction` | Remove dangling deletes | On | Drops delete files that no data file needs any more. Metadata only |
| `system.metadata-compaction` | Rewrite manifests | On: above 100 manifests | Merges small manifests so planning a query reads less metadata |
| `system.orphan-file-removal` | Remove orphan files | Off: older than 3 days | Deletes files under a table's location that no snapshot references, never younger than a day |

Maintenance keeps the policies it creates in a namespace of their own, `_maintenance`, in each database it manages. It holds no tables; you see it next to your namespaces. Don't drop it: Polaris refuses while it holds policies, and **Remove** on the policy page removes it with them.

**Exclusions** switch policy types off for a namespace or a table: maintenance still reads their health. Each is a disabled Polaris policy attached there, which overrides the database's policy, because Polaris resolves a table's policy before its namespace's and a namespace's before the database's. The same order means a policy that another tool attached to a namespace or table inside an excluded namespace still applies there: the table's health shows which policy applied. Maintenance doesn't run an enabled snapshot-expiry or orphan-file policy that it can't follow exactly, such as one without its age in days, with `locations`, or with settings it doesn't know; the table's health gives the reason.

**Policies from other tools count.** A policy attached in Polaris directly, for example by a Spark administrator, to the database, a namespace or a table, applies to the tables below it, and a run follows it. One attached to the database is shown and edited on the policy page, and keeps settings Maintenance does not use. A policy whose values are outside Maintenance's limits, such as orphan files younger than a day, is not applied; the table's page and the run say why.

| Content key | Setting | Limits |
| --- | --- | --- |
| `max_snapshot_age_days`, `min_snapshot_to_keep` (in `config`) | Expire snapshots older than, keep the last | 1 to 3650 days; 1 to 10,000 snapshots |
| `target-file-size-bytes`, `min-input-files`, `delete-ratio-threshold`, `remove-dangling-deletes`, `max-input-bytes-per-run` (in `config`) | Compaction | 16 to 512 MiB; 1 to 1000; 0 to 1; true or false; 16 MiB to 100 GiB |
| `min-count-to-merge` (in `config`) | Rewrite manifests above | 2 to 100,000 |
| `max_orphan_file_age_in_days` | Remove orphan files older than | 1 to 3650 days |

A type without any policy is not maintained. Two administrators who change the same policy don't overwrite each other: Polaris keeps a version per policy, and a save based on an older version is refused.

**Dry run** asks Polaris which policies apply to each table now and shows per table what they would do. It changes nothing.

## Schedule

A database with a policy can have one schedule: a five-field cron expression in UTC, set under **Schedule** on the database's page. A scheduled run applies the policies that apply to each table when it starts, and is skipped while another run of the database is in progress. Pause it by clearing **Enabled**; **Remove** deletes it, and so does removing the policy.

Maintenance runs at most once an hour, where other extensions allow every 5 minutes: a run rewrites files, and the time between two runs is the grace period before orphan cleanup deletes what the previous run expired.

## Runs

A run is a health check (`inspect`), a dry run (`dry-run`) or maintenance (`maintain`), of a whole database or of the tables you name. Its status is one of:

| Status | Meaning |
| --- | --- |
| `queued`, `running` | Not finished yet |
| `success` | Every table succeeded |
| `partial` | Some tables failed; the others were maintained. The next run tries the failed ones again |
| `failed` | The runner reported failure |
| `error` | The platform could not run it: it could not start, exceeded its time limit, or the extension restarted |
| `cancelled` | Stopped on request. Tables it already maintained keep the changes |

`incomplete`, which other extensions use for a run that left work for the next one, never occurs here.

## What a run never does

- Expire a branch or tag head, or ignore a branch's or tag's own retention. They stay until someone removes the branch or tag in the [user portal](user-portal.md).
- Ignore the table properties `history.expire.max-snapshot-age-ms` and `history.expire.min-snapshots-to-keep`: they override the policy, as Iceberg defines.
- Delete files of a table with `gc.enabled=false`: it keeps its history.
- Delete the files of a snapshot it just expired. They become orphans, and orphan cleanup deletes them in a later run. The time between runs is your grace period.
- Delete an orphan file younger than a day, so writes in progress are safe.
- Change a row. Compaction and manifest rewrites commit `replace` snapshots; compaction stamps them with the run in `iceberg-data-platform.maintenance.run-id`.

When a commit conflicts with a writer, such as an [Ingestion](ingestion.md), [Replication](replication.md) or [dbt](dbt.md) run, Maintenance reads the table again, plans the operation against the new snapshot and tries again, up to 4 times with growing waits. If it still conflicts, that table fails on its own; the others continue and the next run tries again.

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
| `list_table_health` / `get_table_health` | Table health from the last runs, with the policies that applied, optionally only tables that need attention, at most `limit` (500) with the `total` that match; one table with its last actions and recent runs. |
| `get_policy` / `update_policy` / `delete_policy` | A database's Polaris policies with their versions, exclusions and schedule, or the defaults; changes them and the exclusions, or creates them from the defaults (team administrators), giving only what changes; removes them with the exclusions and the schedule. |
| `set_schedule` / `delete_schedule` | Maintains a database on a cron schedule in UTC, at most once an hour (team administrators, needs a policy); removes the schedule. |
| `inspect_database` | Reads a database now (writers): `inspect` refreshes table health, `dry-run` plans the policies. Changes no table. `wait` blocks up to 600 seconds. |
| `run_maintenance` | Maintains a database now (writers): applies the policies, optionally to some tables. Marked destructive, so your agent asks first. `wait` blocks up to 600 seconds. |
| `list_runs` / `get_run` / `get_run_logs` / `cancel_run` | A database's runs; one run with each table's actions, optionally waiting for it to finish; the last lines of its log; stops a run (writers). |

Every tool returns what the REST operation of the same capability returns. Times are RFC 3339 strings in UTC, such as `2026-11-24T09:15:02.123Z`. An error has a readable `error` and a stable `code`, such as `policy_required` when you maintain or schedule a database without a policy, or `revision_conflict` when a policy changed since you read it.

What each role may do is in [roles and permissions](reference/roles.md#extensions).

## Isolation and limits

- Each run is a container started by the extension's launcher, the only part with the Docker socket: read-only, no capabilities, memory and CPU limits (`RUN_MEMORY`, `RUN_CPUS`), and a private network that holds only the platform's catalog and storage. Runs never reach the internet. On Kubernetes each run is a Job in a namespace of its own, whose network policy allows only the catalog, storage and the launcher.
- A run acts as the team's service account for the environment, with a read token for health checks and dry runs and a write token for maintenance. It ends 15 minutes before that token expires (`POLARIS_TOKEN_LIFETIME`, one hour by default). Policies are read with a read token and changed with a write token of the same account.
- One database has at most one run at a time; `MNT_MAX_CONCURRENT_RUNS` (2) limits runs across teams.
- Compaction holds about one file group in memory. Raise `RUN_MEMORY` with larger target file sizes.
- Not in this version: record expiration (row TTL) and table statistics. See the extension's [decisions](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/maintenance/DECISIONS.md).
