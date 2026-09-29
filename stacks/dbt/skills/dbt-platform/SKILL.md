---
name: dbt-platform
description: Build, test and schedule dbt v2 pipelines on the Iceberg Data Platform through the dbt stack's MCP tools. Use when a user asks to model, transform, clean or aggregate data in their team's Iceberg databases, to add a dbt model or test, to fix a failing dbt run, or to schedule or promote a pipeline.
---

# dbt pipelines on the Iceberg Data Platform

The dbt stack's MCP server (`dbt`, at `http://localhost:3004/mcp`) runs dbt v2 OSS with DuckDB on
the team's Apache Iceberg databases. Every tool acts with the signed-in user's team role; builds
run as the team's service account.

## Workflow

1. `whoami`: find the team, your role there (reader, writer or admin) and its databases per
   environment. `list_environments` shows where dbt is enabled; only a team admin can
   `enable_environment`.
2. `list_projects`, or `create_project` (writer). A new project is a runnable starter: a seed,
   a staging model and a mart with tests. Build it once to see the pipeline.
3. Change code on a branch: `create_branch`, then `write_files` with full file contents
   (one commit per call; `null` deletes). `main` is never written directly.
4. `compile` the changed models, then `run` with `command="build"`, `ref=<branch>` in
   development. On failure read `get_run_logs`, fix, commit again, and rebuild with
   `select="<model>+"`.
5. Check the result: `preview_model` for rows, `get_pipeline` for the DAG and statuses,
   `get_model` for columns and tests.
6. Ask a team admin to `merge_to_main`. Acceptance and production build only `main`; ask the
   user before building or scheduling there.

## Project rules

- `schema` is the Iceberg namespace. `+catalog_name` picks the team database by its display name,
  with `-` written as `_`. The same code runs in every environment.
- Keep `+materialized: replace_table`: it replaces rows in one Iceberg transaction and keeps the
  table's identity, data shares and docs. Use `incremental` (`unique_key`, `merge`) for large or
  append-only data. Iceberg views are not supported. Use `full_refresh=true` only when a table's
  columns change.
- Read other tables of the team with `sources:` (`database:` is the database's catalog name).
  Received data shares are not readable by runs.
- Never add `profiles.yml`, `catalogs.yml` or `packages.yml`.
- Describe every model and its important columns in YAML, and add `not_null`/`unique`/
  `accepted_values`/`relationships` tests. Descriptions become the table comment and column docs
  in the platform catalog; test results become the table's quality status.
- SQL is DuckDB SQL.
