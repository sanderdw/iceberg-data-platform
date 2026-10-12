# Decisions for Ingestion (dlt) (spike, 2026-10-12)

The spike ran dlt 1.31.0 with PyIceberg 0.12.0 against Apache Polaris and RustFS of platform 0.8.0.
Its choices hold for Bridge contract 0.1. Only the runner image and this extension depend on them.

## Why dlt

Apache SeaTunnel, dlt and Apache NiFi were compared. dlt (Apache-2.0, the open-source
`dlt[pyiceberg]`; the paid dltHub tier is not used) is a Python library, so it fits the platform's
Python, uv and PyIceberg stack. Its REST, SQL and file sources are declarative, so pipelines can be
configuration rather than code. Batch only. SeaTunnel stays the candidate for bulk database copies
and change data capture, which would need long-lived credentials from the Bridge.

The earlier proposal in the platform (`docs/proposals/ingestion-extension.md`) used DuckDB and dlt
only as readers, with the extension's own writer and a forwarder per endpoint. This extension uses
dlt's own Iceberg destination instead, which keeps the code small.

## dlt's Iceberg destination on Polaris: what it needs

dlt's `filesystem` destination with `table_format="iceberg"` loads through PyIceberg and the REST
catalog given in `iceberg_catalog.iceberg_catalog_config`, which it passes to `load_catalog`
unchanged. Out of the box it does not fit Polaris with vended credentials:

- **dlt writes its own tables as plain files under `bucket_url`.** `_dlt_loads`, `_dlt_version`
  and `_dlt_pipeline_state`, and its init markers, go through fsspec, not the catalog. That needs
  storage keys for the bucket, which the Bridge never hands out (vended credentials are per table).
- **dlt derives each table's location from `bucket_url`.** Polaris only accepts locations under the
  database's `default-base-location`.

What works, verified against Polaris:

| Setting | Effect |
| --- | --- |
| `bucket_url = file:///work/dlt` (the run's disk) | dlt's own tables and markers stay in the run, never in the team's namespace, and no storage keys are needed |
| `dlt.common.libs.pyiceberg.create_table` wrapped to pass `table_location=None` | Polaris places a new table under the database's base location |
| `RESTORE_FROM_DESTINATION=false` | dlt does not look for its state in the destination; it uses its pipelines folder |
| The extension stores `state.json` and `schemas/` of the pipelines folder after each successful run, and stages them into the next | Incremental cursors carry over (6 → 9 in the spike); a failed run keeps the previous state |
| `py-io-impl` that overrides `s3.endpoint` | Polaris vends the browser-facing storage address; the run uses the internal one (as the platform's notebooks do) |

`replace` is PyIceberg's `overwrite` and `merge` (strategy `upsert`) is PyIceberg's `upsert`, so
the table keeps its UUID; data shares and table properties survive. dlt's `refresh` modes and
`drop_sources` are not offered: they drop tables, and Polaris refuses a purge unless
`DROP_WITH_PURGE_ENABLED`. `full_refresh` instead runs without state and with `replace`.

Known limits, from dlt and PyIceberg:

- `merge` upserts in batches of 1,000 rows, one commit each: fine for increments, slow for large
  first loads. Load large tables with `replace` first.
- Upsert needs unique keys in each batch and does not evolve the schema of nested types.
- dlt adds `_dlt_load_id` and `_dlt_id` to every table, and child tables `<table>__<field>` for
  nested lists, joined on `_dlt_parent_id`.

## Tokens and run time

A run gets one `write` token of the team's automation principal as an environment variable; the
catalog configuration uses it directly. Vended storage credentials last as long as the token
(`POLARIS_TOKEN_LIFETIME`, one hour by default), so, as in the dbt extension, a run ends 15 minutes
before the token expires. A custom PyIceberg `AuthManager` that renews the token is not needed for
runs that short.

## Isolation and the network

Runs reuse the dbt extension's launcher: the only container with the Docker socket, fixed image,
flags and mounts, one container per run (read-only, no capabilities, user 10001, memory, CPU and
process limits). Each run gets two networks:

- an internal one with the catalog and storage under their contract aliases, found by role label;
- its own egress network to reach the source.

The platform's other services (`bridge`, `keycloak`, `postgres`) are on neither, so a run cannot
resolve them. Sources are checked twice:

- **When saved:** loopback, private, link-local and local host names are refused, and so are names
  that resolve to such addresses.
- **In the run:** a guard on `getaddrinfo` refuses every such address the run resolves, also for
  redirects and next-page links. Only the catalog and storage aliases are allowed.

`ING_ALLOW_PRIVATE_SOURCES=true` lifts this for laptop demos, and the launcher then adds
`host.docker.internal`.

## Pipelines are configuration

v1 accepts only the declarative `rest_api`, `sql_database` and `filesystem` sources, with an
allowlist of keys per type. A REST path must stay below `base_url` (dlt would follow an absolute URL
past the address checks). Database connections take host, port, database, user, a secret password
and a TLS mode, never a URL or driver options, so `local_infile`, `init_command`, unix sockets and
certificate file paths cannot be set. PostgreSQL goes through pg8000 and MySQL/MariaDB through
PyMySQL: no copyleft drivers.

Custom Python pipelines and dlt's verified sources are later work. They need a per-run container
built from reviewed code.

## Secrets

Secret values never appear in a definition: it refers to them as `$secret:<name>`. They are
encrypted with AES-GCM under `ING_SECRET_KEY` (setup generates it), with the secret's name as
associated data. They can be set through the page, the REST API and MCP, but never read back. A
secret reaches a run only as an environment variable and is masked in the run's log twice: by the
runner and by the gateway. Secrets belong to where a pipeline connects: changing the host removes
them, so a changed definition cannot send an old secret elsewhere.
