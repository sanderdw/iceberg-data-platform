---
name: maintenance
description: Keep a team's Iceberg tables healthy on the Iceberg Data Platform with the Maintenance extension's MCP tools (beta) - find tables with small files, old snapshots or delete files, dry-run and set maintenance policies and schedules, and run snapshot expiry, compaction and orphan cleanup. Use when a user asks why queries got slow, about small files, compaction, snapshot expiry, storage growth or table maintenance.
---

# Maintenance on the Iceberg Data Platform (beta)

The Maintenance extension's MCP server (`iceberg-maintenance`, at `http://localhost:3013/mcp`) keeps
a team's Apache Iceberg tables tidy, one policy per database. Every tool acts with the signed-in
user's team role; runs execute as the team's service account. People follow it at
`http://localhost:3013`.

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

1. `whoami`: teams, your role, databases per environment, `enabledEnvironments` and the policy
   `defaults`. Nothing runs where Maintenance is not enabled; a team administrator enables it with
   `enable_environment` (ask the user first: runs act for the whole team).
2. `list_databases`, then `list_tables` with `attention=true`. Health comes from the last run that saw
   a table; if a database has none or it is old, `run_maintenance` with `mode="inspect"` and `wait=120`
   refreshes it. It only reads.
3. Explain what you found in plain words: small files slow queries down, snapshots keep old files and
   time travel, delete files are applied by compaction.
4. `get_policy`. Propose changes and `run_maintenance` with `mode="dry-run"`, then `get_run` to show
   per table what would happen. Nothing changes in a dry run.
5. With the user's agreement, a team administrator sets the policy with `set_policy` (only the settings
   that change) and a schedule (`cron`, UTC, at most hourly). Exclude tables that must keep their full
   history, or set the table properties `history.expire.*` on them.
6. Maintain now only when the user asks: `run_maintenance` with `mode="maintain"` (needs a policy), then
   `get_run` and `get_run_logs`.

## Rules

- Expired snapshots can no longer be used for time travel. Say so before you shorten retention.
- Branch and tag heads are never expired. Their snapshots stay until someone removes the branch or tag.
- Orphan cleanup is off by default and never deletes files younger than a day. Files of expired
  snapshots are deleted by orphan cleanup in a later run.
- Ask before maintaining in acceptance or production, before changing a policy and before any tool
  marked destructive.
