---
name: maintenance
description: Keep a team's Iceberg tables healthy on the Iceberg Data Platform with the Maintenance extension's MCP tools (beta) - find tables with small files, old snapshots or delete files, dry-run and set maintenance policies and schedules, and run snapshot expiry, compaction and orphan cleanup. Use when a user asks why queries got slow, about small files, compaction, snapshot expiry, storage growth or table maintenance.
---

# Maintenance on the Iceberg Data Platform (beta)

The Maintenance extension's MCP server (`iceberg-maintenance`, at `http://localhost:3013/mcp`) keeps
a team's Apache Iceberg tables tidy by Apache Polaris policies. Every tool acts with the signed-in
user's team role; runs, and reading and writing policies, act as the team's service account. People
follow it at `http://localhost:3013`.

## Connect

```bash
claude mcp add --transport http --client-id ext-maintenance-mcp --callback-port 3010 iceberg-maintenance http://localhost:3013/mcp
```

On a Kubernetes installation the address is `https://maintain.<domain>/mcp` instead (the page at
`https://maintain.<domain>`); ask the user for the domain. Other agents use the same address and
client id (`AGENTS.md` in the installation folder shows the recipes). If the address does not answer,
the extension is not installed: stop and tell the user to run the installer again with
`ICEBERG_EXTENSIONS=maintenance`, or on Kubernetes to register it and install its chart
(`kubernetes-install` skill, or the documentation's "Add extensions").

## Workflow

1. `whoami`: teams, your role, databases per environment, `enabledEnvironments`, the `policyTypes` and
   their `defaults`. Nothing runs, and no policy can be read, where Maintenance is not enabled; a team
   administrator enables it with `enable_environment` (ask the user first: runs act for the whole team).
2. `list_databases`, then `list_table_health` with `attention=true`. It returns at most `limit` tables
   (500 by default) and `total`, the number that match: narrow by `database` when there are more.
   Health comes from the last run that saw a table; if a database has none or it is old,
   `inspect_database` with `wait=120` refreshes it. It only reads.
3. Explain what you found in plain words: small files slow queries down, snapshots keep old files and
   time travel, delete files are applied by compaction. `get_table_health` shows one table in detail.
4. `get_policy`: per type (`snapshotExpiry`, `dataCompaction`, `metadataCompaction`,
   `orphanFileRemoval`) the settings, the Polaris policy behind them with its `version`, and the
   `exclusions`. `get_table_health` shows which policies applied to a table at its last run and where
   each is attached (`appliedAt`: catalog, namespace or table). Propose changes and `inspect_database`
   with `mode="dry-run"` and `wait=120`: its `results` show per table what would happen and which policy
   decided it. Nothing changes in a dry run, and a type without a policy does nothing.
5. With the user's agreement, a team administrator changes the policy with `update_policy` (only the
   settings that change; a type without a policy is created from the defaults). Pass the versions you read
   as `expected_versions`; `revision_conflict` means someone changed it: read it again. Ages are in days.
   To leave a namespace or table alone, add it to `exclusions` (`{target, level, types}`; all types when
   `types` is left out; the list replaces the old one). For tables that must keep their full history you
   can also set the table properties `history.expire.*`. Policies another tool attached in Polaris count
   too; `exclusion_conflict` means a table already has its own policy of that type.
6. A schedule is separate from the policy and needs one: `set_schedule` with a five-field `cron` in
   UTC, at most once an hour (`enabled=false` pauses it), and `delete_schedule` to remove it.
   `get_policy` and `list_databases` show it as `schedule`, with `nextRunAt` and `lastRun`.
7. Maintain now only when the user asks: `run_maintenance` (needs a policy; it always maintains), then
   `get_run` and `get_run_logs`.

## Runs

- `inspect_database` and `run_maintenance` return the run at once, usually `queued`. Pass `wait` (up to
  600 seconds) to get it back finished, or call `get_run` with `wait`. Waiting never fails: when the
  time is up you get the run as it is.
- A run's `status` is `queued`, `running`, `success`, `partial` (some tables failed, the others were
  maintained), `failed` (the runner reported failure), `error` (the platform could not run it),
  `cancelled` or `incomplete` (other extensions use it; Maintenance does not).
- `get_run` returns each table's actions in `results`; `list_runs` leaves them out. `get_run_logs`
  returns the last `limit` lines. `cancel_run` stops a run; a finished one answers `run_finished`.
- Times are RFC 3339 strings in UTC, such as `2026-11-24T09:15:02.123Z`.
- Without a policy, `run_maintenance` and `set_schedule` answer `policy_required`.
- A commit that conflicts with another writer is planned and retried; an action's `retries` says how often.

## Rules

- Expired snapshots can no longer be used for time travel. Say so before you shorten retention.
- Branch and tag heads are never expired. Their snapshots stay until someone removes the branch or tag.
- Orphan cleanup is off by default and never deletes files younger than a day. Files of expired
  snapshots are deleted by orphan cleanup in a later run.
- Ask before maintaining in acceptance or production, before changing a policy or a schedule and
  before any tool marked destructive (`run_maintenance`, `delete_policy`, `delete_schedule`,
  `disable_environment`). `inspect_database` is not: it changes no table.
