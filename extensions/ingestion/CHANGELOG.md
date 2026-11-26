# Changelog

## Unreleased

The REST API and the MCP tools follow the platform's one set of conventions. Nothing of the old shapes
is kept: update scripts and agent instructions.

- **Bridge contract 1.0.** Needs the platform 1.0.0 or later (Bridge contract `>=1.0,<2`); it no longer
  starts against a 0.9 platform. A Bridge error keeps its status and code (a 503 or 500 was 502).
- **File downloads.** `ING_MAX_DOWNLOAD_GB` (`runs.maxDownloadGB` on Kubernetes) sets the largest file an
  https or http file source downloads: 4 GiB by default (was a fixed 2 GiB). On Kubernetes a run's work
  volume (`runs.workSize`) is 8Gi (was 4Gi), so such a file fits.
- **Apps menu.** Drawing the menu again no longer leaves the previous menu's listeners on the page.
- **Lists** are an object with one key, in REST as in MCP: `{"pipelines": [...]}`, `{"templates": [...]}`,
  `{"runs": [...]}` and `{"environments": [...]}` (REST returned bare arrays).
- **Runs.** Starting a run returns 202 with the run as `get_run` returns it. The tables a run wrote are
  in `results`, beside `summary` (was `summary.tables`); a list of runs leaves `results` out.
  `GET /runs/{run}` and `get_run` take `wait` (0 to 600 seconds). A run has `scheduled` (was
  `schedule`). Run logs take `limit` (was `tail`).
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z` (were
  seconds since 1970). A schedule's `nextRunAt` is `null` while it is not enabled, and a schedule has
  `createdAt`.
- **REST bodies are camelCase:** `expectedRevision` (was `expected_revision`) and `fullRefresh` (was
  `full_refresh`). The tools keep `expected_revision` and `full_refresh`.
- **Delete** is `DELETE /pipelines/{pipeline}?confirmName=<name>` (was `POST /pipelines/{pipeline}/delete`
  with a JSON body) and returns `{"deleted": true, "pipeline": "<id>"}` (was `id`).
- **Secrets** are set with `PATCH /pipelines/{pipeline}/secrets` (was `PUT`): the call merges.
- **Environments.** `enable_environment` and `disable_environment` return the environment as
  `list_environments` shows it: `{team, environment, enabled, enabledBy, databases}`. Disabling one that
  is not enabled is 409 `environment_not_enabled` (was 404). The service account's id is no longer
  returned.
- **`whoami`** returns only what this extension shows about you; `sharedDatabases` is gone.
- **Errors.** A database in the body that is not the team's in that environment is 422
  `invalid_database` (was `unknown_database`). An unknown path is 404 `not_found`, a wrong method 405
  `method_not_allowed` and an unexpected error 500 `internal_error`, all as `{"error", "code"}`.
- `update_pipeline` and `set_pipeline_secrets` are marked destructive, so an agent asks first: they can
  remove secrets and the load state.
- The OpenAPI contract describes every response and the error body.

Apache Polaris 1.9:

- **Long loads keep their storage access.** A run renews a table's storage credentials shortly before
  they expire, through Polaris's credentials endpoint.
- A run sets its table properties again when a concurrent commit got there first (409), instead of
  reporting the table under `published.errors`.

## 0.1.4 - 2026-11-18

- Updated dependencies, including FastAPI 0.162.4, croniter 7.0.0 and uv 0.13.8.

## 0.1.3 - 2026-11-09

- **OpenTelemetry.** With `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env` (`otel.endpoint` on Kubernetes) the gateway sends its logs and traces over OTLP/HTTP, as the platform does. Off by default. See [Send logs and traces to OpenTelemetry](https://sanderdw.github.io/iceberg-data-platform/observability/).
- dlt 1.32 and updated dependencies.

## 0.1.2 - 2026-10-27

- certifi 2026.10.23 removes the distrusted eMudhra emSign roots from the Python HTTP clients.
- Updated dependencies.

## 0.1.1 - 2026-10-17

- **Runs on Kubernetes.** The chart `deploy/helm/iceberg-ingestion` installs Ingestion next to the
  platform's chart. Each run is a Kubernetes Job in a namespace of its own that reaches public sources
  only (`runs.allowPrivateSources` for sources in your own network); the runner fetches its job and
  returns its state through the launcher (`LAUNCHER_BACKEND=kubernetes`). The Compose installation is unchanged.
- Secrets are masked in run logs and errors also when URL-encoded or escaped, Basic credentials are
  masked, and the query string of a failed request's URL is hidden, since an API key can travel there
  under any name.

## 0.1.0 - 2026-10-13

- First version of Ingestion (dlt) for the Iceberg Data Platform, on Extension Bridge contract 0.1.
- Declarative dlt 1.31 pipelines, never code: REST APIs (dlt's `rest_api` source with bearer, API
  key or basic sign-in, paginators and incremental parameters), PostgreSQL (pg8000) and MySQL or
  MariaDB (PyMySQL) tables with an optional incremental cursor, and CSV, JSONL or Parquet files over
  HTTPS or from S3-compatible storage.
- Loads with dlt's Iceberg destination through Polaris as the team's automation principal, with a
  short-lived write token and vended storage credentials. Each resource appends, replaces or merges
  (upsert on a primary key), with optional partition columns. Replace and merge keep the table and
  its identity, so data shares and descriptions stay. dlt's own bookkeeping stays with the run,
  never in the team's namespace; the extension keeps dlt's state between runs, so incremental
  cursors carry over. `full_refresh` loads everything again.
- Every loaded table shows **Produced by dlt** in the platform catalog: run, time, cursor, node and
  quality from the row count, with a link back to the pipeline.
- Runs in isolated containers started by a launcher, the only component with the Docker socket:
  read-only, no capabilities, memory, CPU and process limits, a private network holding only the
  platform's catalog and storage, and a separate network to reach the source. Sources on loopback,
  private or link-local addresses are refused when saved and again for every address a run
  resolves, unless `ING_ALLOW_PRIVATE_SOURCES=true` (laptop demos). A run ends 15 minutes before its
  token expires (`POLARIS_TOKEN_LIFETIME`).
- Source secrets are encrypted with AES-GCM, write-only through the page, the REST API and MCP,
  passed to a run only in its environment and masked in logs. Changing where a pipeline connects
  removes its secrets.
- Team administrators create, change and delete pipelines, set secrets and schedules and enable
  environments; writers run and stop pipelines; readers view. Cron schedules in UTC never overlap a
  running pipeline.
- Templates: CBS StatLine open data (OData, paginated JSON), a PostgreSQL table incremental on
  `updated_at`, MySQL or MariaDB tables, and airport codes as CSV over HTTPS.
- REST API and MCP server with the same capabilities, and a pipelines page.
