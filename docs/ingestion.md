# Ingestion (dlt)

Get data in: declarative [dlt](https://dlthub.com/docs) pipelines that load REST APIs, PostgreSQL and MySQL/MariaDB tables, and CSV, JSONL or Parquet files into a team's Apache Iceberg databases, on demand or on a schedule. People use the pipelines page; AI agents and scripts use the same capabilities through MCP and a REST API.

| | |
| --- | --- |
| Sources | `rest_api`, `sql_database` (PostgreSQL, MySQL, MariaDB), `filesystem` (HTTPS, S3) |
| Page | `http://localhost:3008` (pipelines, secrets, runs, logs, schedules), linked from the user portal as **Ingestion (dlt)** |
| API | `http://localhost:3008/api/v1` |
| MCP | `http://localhost:3008/mcp` (Keycloak sign-in through the `ext-ingestion-mcp` client) |

## Install

The installer asks whether to add Ingestion. To install without questions:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | ICEBERG_EXTENSIONS=ingestion sh
```

```powershell
$env:ICEBERG_EXTENSIONS = 'ingestion'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

A team administrator enables it once per environment with **Enable** on the page, creates pipelines and enters their secrets, such as an API key or a database password. Secrets are write-only: no page, API or MCP response returns them. Writers start runs; a schedule starts them too.

`ingestion/.env` in the installation directory holds `ING_SECRET_KEY`, which decrypts the pipelines' secrets. Rerunning the installer keeps it, with the extension, its pipelines and their runs.

## Pipelines

A pipeline belongs to one team and environment, and loads into one of the team's databases there, into one namespace (dlt's dataset, such as `raw_crm`). Pipelines are configuration, never code.

| Source type | What it reads |
| --- | --- |
| `rest_api` | dlt's declarative REST source: `base_url`, sign-in (bearer, API key, basic), a paginator, endpoints with `data_selector`, parameters, nested endpoints and an incremental parameter |
| `sql_database` | Tables of a PostgreSQL (pg8000) or MySQL/MariaDB (PyMySQL) database: host, port, database, user, a secret password, TLS (`verify-full`, `require` or `disable`), optionally an incremental cursor column per table |
| `filesystem` | One CSV, JSONL or Parquet file over HTTPS, or files matching a pattern in S3-compatible storage, optionally only those changed since the last run |

Per resource (endpoint, table or file), `writeDisposition` is `append`, `replace` or `merge` (upsert on `primaryKey`), and `partition` lists identity partition columns. Replace and merge keep the table and its identity, so data shares and descriptions stay.

dlt adds the columns `_dlt_load_id` and `_dlt_id` to each table, and child tables named `<table>__<field>` for nested lists. Its own bookkeeping tables stay with the run, never in your namespace. The catalog shows every loaded table as **Produced by dlt**, with a link back to the pipeline, and the next run continues from the last cursor.

Secrets (API keys, tokens, passwords) never go in a definition. Refer to them as `"$secret:<name>"` and set them on the pipeline's page, with `PUT /api/v1/pipelines/{id}/secrets`, or with the MCP tool `set_pipeline_secrets`. They are encrypted, write-only and masked in run logs. Changing where a pipeline connects removes its secrets.

Templates to start from: CBS StatLine open data (REST, paginated OData), a PostgreSQL table incremental on `updated_at`, MySQL or MariaDB tables, and airport codes as CSV over HTTPS.

| Role in the team | Allowed |
| --- | --- |
| reader | View pipelines, runs, logs and schedules; validate a definition |
| writer | Also run pipelines and stop runs |
| admin, bucket-admin | Also create, change and delete pipelines, set secrets and schedules, enable environments |

## Connect an AI agent

```bash
claude mcp add --transport http --client-id ext-ingestion-mcp --callback-port 3010 iceberg-ingestion http://localhost:3008/mcp
```

The `ingestion` [agent skill](agent-skills.md) in the installation folder guides an agent from a template to a first load. Register the [user portal's MCP server](ai-agents.md) too, to read the loaded tables.

## Isolation and limits

- Each run is its own container from the pinned runner image, with a read-only file system, that writes as the team's service account. It reaches the catalog, storage and the internet, but not the platform's other services.
- Sources on loopback, private or link-local addresses (including cloud metadata) are refused when a pipeline is saved, and again for every address a run resolves, redirects included. For a demo against a database on your own computer, set `ING_ALLOW_PRIVATE_SOURCES=true` in `ingestion/.env` and restart the extension with `docker compose -f ingestion/compose.yaml up -d --wait`; the run then reaches your computer as `host.docker.internal`.
- A run ends 15 minutes before its catalog token expires: 45 minutes with the platform's default `POLARIS_TOKEN_LIFETIME` of one hour. Raise that in `.env`, for example to 7200, for longer loads; see [configuration](configuration.md).
- At most 4 runs at once, 2 per team, and one per pipeline. A schedule skips its time while the pipeline is still running.

Ingestion is batch only, with the three declarative sources. Custom Python pipelines, dlt's verified sources and change data capture are not included.

How it is built: [Ingestion internals](develop/ingestion.md).
