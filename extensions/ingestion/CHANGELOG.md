# Changelog

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
