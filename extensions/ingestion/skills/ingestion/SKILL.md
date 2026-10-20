---
name: ingestion
description: Load data from REST APIs, PostgreSQL or MySQL/MariaDB tables, or CSV, JSONL and Parquet files into a team's Iceberg databases on the Iceberg Data Platform, with the Ingestion (dlt) extension's MCP tools. Use when a user asks to bring in, import, sync, copy or schedule data from an API, an operational database or a file, or to fix a failing ingestion run.
---

# Ingestion (dlt) on the Iceberg Data Platform

The Ingestion extension's MCP server (`iceberg-ingestion`, at `http://localhost:3008/mcp`) runs
declarative dlt pipelines that load into the team's Apache Iceberg databases. Every tool acts with
the signed-in user's team role; runs execute as the team's service account. People follow the
pipelines at `http://localhost:3008`; each pipeline's `url` opens it there.

## Connect

```bash
claude mcp add --transport http --client-id ext-ingestion-mcp --callback-port 3010 iceberg-ingestion http://localhost:3008/mcp
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
```

The user portal's server (`iceberg-user`) reads the loaded tables. On a Kubernetes installation the
addresses are `https://ingest.<domain>/mcp` and `https://users.<domain>/mcp` instead (the pipelines
page at `https://ingest.<domain>`); ask the user for the domain. Other agents use the same addresses
and client ids (`AGENTS.md` in the installation folder shows the recipes). If the Ingestion address
does not answer, the extension is not installed: stop and tell the user to run the installer again
with `ICEBERG_EXTENSIONS=ingestion`, or on Kubernetes to register it and install its chart
(`kubernetes-install` skill, or the documentation's "Add extensions").

## Workflow

1. `whoami`: the team, your role (reader, writer or admin), its databases per environment and
   `enabledEnvironments`. Nothing runs where Ingestion is not enabled; a team administrator enables
   it with `enable_environment` (ask the user first: runs act for the whole team).
2. Ask what to load, from where, how often, and whether rows change (merge on a key) or only grow
   (append) or are replaced each time (replace). Land outside data as-is in a raw namespace such as
   `raw_<system>`; shape it later with the dbt extension.
3. `list_templates`, pick the closest, and adapt `source` and `resources`. Never put a secret value
   in the definition: write `"$secret:<name>"`.
4. `validate_pipeline` until it is valid, then `create_pipeline` (team administrators). Read the
   `hint`.
5. Secrets: ask the user to enter them on the pipeline's page (`url`). Use `set_pipeline_secrets`
   only with a value the user gave you for exactly that, and never repeat it back.
6. `run_pipeline` with `wait` (writers). On `failed`, read `get_run` and `get_run_logs`, fix the
   definition with `update_pipeline`, and run again. `full_refresh` loads everything again.
7. Check the result with the user portal tools (`describe_table`, `preview_rows`). The table shows
   "Produced by dlt" in the catalog.
8. Offer a schedule (`set_schedule`, cron in UTC, at most every 5 minutes), then a dbt staging model
   on the raw tables (the `dbt-platform` skill) and a semantic model for the mart.

## Definitions

- `rest_api`: `client.base_url`, optional `auth` (`bearer` with `token`, `api_key` with `name`,
  `location` and `api_key`, `http_basic` with `username` and `password`), `paginator` (`json_link`,
  `header_link`, `offset`, `page_number`, `cursor`, `single_page`, `auto`), and `resources` with a
  `name` and an `endpoint`: `path` below `base_url`, `params`, `data_selector`, and `incremental`
  (`cursor_path`, `start_param`, `initial_value`).
- `sql_database`: `dialect` (`postgresql` or `mysql`), `host`, `port`, `database`, `username`,
  `password` (a secret), `tls` (`verify-full`, `require`, `disable`), `schema`, and `tables` with
  an optional `incremental.cursor` column. Ask for a read-only account. A cursor misses deleted
  rows: run with `full_refresh` now and then.
- `filesystem`: `url` of one file over HTTPS, or `s3://bucket/prefix/` with `file_glob` and `s3`
  (`endpoint_url` or `region`, `access_key_id`, `secret_access_key` as a secret); `format` `csv`,
  `jsonl` or `parquet`; `table`; `incremental` (only files changed since the last run, S3 only).
- `resources`: per resource name, `writeDisposition` (`append`, `replace`, `merge`), `primaryKey`
  (needed for merge) and `partition`.

## Rules

- Pipelines are configuration only; there is no way to run code.
- Sources on private or local addresses are refused unless the platform administrator allowed them.
- dlt adds `_dlt_load_id` and `_dlt_id` columns, and child tables `<table>__<field>` for nested lists.
- Changing a pipeline's host removes its secrets; changing its database or namespace starts it from
  scratch.
- Ask before running in acceptance or production, before setting secrets, and before
  `delete_pipeline`, `delete_schedule` or `disable_environment`.
