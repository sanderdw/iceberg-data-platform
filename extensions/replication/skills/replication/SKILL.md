---
name: replication
description: Keep an operational PostgreSQL database in step with a team's Iceberg databases on the Iceberg Data Platform, with change data capture through the Replication (OLake) extension's MCP tools. Use when a user asks to replicate, mirror, sync or stream a database's tables (including updates and deletes) into Iceberg, to set up change data capture, or to fix a failing replication run. For REST APIs, files and one-time loads, use the ingestion skill instead.
---

# Replication (OLake) on the Iceberg Data Platform (beta)

The Replication extension's MCP server (`iceberg-replication`, at `http://localhost:3014/mcp`)
keeps operational databases in step with the team's Apache Iceberg databases. Each run reads the
changes since the last one with OLake and writes them as the team's service account. Every tool
acts with the signed-in user's team role. People follow replications at `http://localhost:3014`;
each replication's `url` opens it there.

## Connect

```bash
claude mcp add --transport http --client-id ext-replication-mcp --callback-port 3010 iceberg-replication http://localhost:3014/mcp
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
```

The user portal's server (`iceberg-user`) reads the replicated tables. On a Kubernetes installation
the addresses are `https://replicate.<domain>/mcp` and `https://users.<domain>/mcp`; ask the user for
the domain. If the Replication address does not answer, the extension is not installed: stop and
tell the user to run the installer again with `ICEBERG_EXTENSIONS=replication`.

## Replication or Ingestion

Replication is for databases that keep changing: it follows inserts, updates and deletes. For a
REST API, files, or a one-time or occasional load of a table, use the `ingestion` skill (dlt).

## Workflow

1. `whoami`: the team, your role (reader, writer or admin), its databases per environment and
   `enabledEnvironments`. Nothing runs where Replication is not enabled; a team administrator
   enables it with `enable_environment` (ask the user first: runs act for the whole team).
2. Ask for the source (host, port, database, a user with the REPLICATION attribute), which tables,
   and how fresh the copy must be. Land them in a raw namespace such as `raw_<system>`.
3. `list_templates` and adapt `postgres-cdc`: `source` with `password` as `"$secret:password"`,
   `slot` and `publication` for change data capture. `create_replication` (team administrators);
   `tables` may stay empty for now.
4. Ask the user to enter the password on the replication's page (`url`). Use
   `set_replication_secrets` only with a value the user gave you for exactly that, and never repeat
   it back.
5. `check_source` (writers). Every `required` check must pass. If one fails, give the user the
   replication's `setupSql` for the database owner (wal_level = logical needs a server restart),
   then check again. `tables` lists what can be replicated, with primary keys and cursor columns.
6. `update_replication` with `tables`: per `schema.table`, `mode` `cdc` (needs slot and
   publication, and the table in the publication), `incremental` (with `cursor`, such as
   `updated_at`; misses deletes) or `full_refresh`. Optional `table` (the Iceberg name),
   `partition` (such as `"/{created_at,month}"`) and `appendOnly`. Read `warnings`: a partitioned
   change-capture table needs `REPLICA IDENTITY FULL` at the source.
7. `run_replication` with `wait`. `partial` means the first copy stopped at the time limit; run
   again and it continues. On `failed`, read `get_run` and `get_run_logs`; an out-of-memory stop
   needs the platform administrator (`RUN_MEMORY`).
8. Check the result with the user portal tools (`describe_table`, `preview_rows`); current rows are
   `_op_type <> 'd'`. Offer a schedule (`set_schedule`, such as `*/15 * * * *` in UTC), then a dbt
   staging model on the raw tables (`dbt-platform` skill) that filters deleted rows.

## Rules

- Updates and deletes are written as positional deletes (`deleteMode` `pos`) or deletion vectors
  (`dv`, Iceberg v3); equality deletes are not available, because notebooks could not read them.
- A row deleted in the source stays with `_op_type = 'd'`. OLake adds `_olake_id`,
  `_olake_timestamp`, `_op_type`, `_cdc_lsn` and `_cdc_timestamp`.
- Sources on private addresses run only inside the networks the platform administrator allows.
- Changing the host, user, slot, database or namespace makes the next run copy everything again;
  a new host or user removes the password. `full_refresh` copies everything again on purpose.
- After `delete_replication`, give the database owner the returned `dropSlotSql`: an abandoned slot
  makes the source keep its change log until its disk fills.
- Ask before running in acceptance or production, before setting secrets, and before
  `delete_replication`, `delete_schedule` or `disable_environment`.
