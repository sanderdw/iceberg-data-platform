# Changelog

## Unreleased

The REST API and the MCP tools follow the platform's one set of conventions. Nothing of the old shapes
is kept: update scripts and agent instructions.

- **Account menu.** The header ends the same way as in the administration and user portals: **Read-only** when
  you have the read role in each of your teams, then Settings, Apps and your name. Your name opens your teams and
  roles, **Getting started** (this extension's documentation), the theme switch and **Sign out**. Settings says what
  turning it on and off does and who can change it, and looks the same in every extension.
- **Settings.** Team administrators enable and disable Replication per environment under **Settings** ›
  **Environments** (the header link), the page every extension shares, which also shows who enabled each one. Disabling was only possible
  through MCP or REST. A replication in an environment where Replication is off says so, with Enable for
  administrators; the start page's "Where replications can run" card is gone.
- **Bridge contract 1.0.** Needs the platform 1.0.0 or later (Bridge contract `>=1.0,<2`); it no longer
  starts against a 0.9 platform. A Bridge error keeps its status and code (a 503 or 500 was 502).
- **Apps menu.** Drawing the menu again no longer leaves the previous menu's listeners on the page.
- **Lists** are an object with one key, in REST as in MCP: `{"replications": [...]}`, `{"templates": [...]}`,
  `{"runs": [...]}` and `{"environments": [...]}` (REST returned bare arrays).
- **Runs.** Starting a run returns 202 with the run as `get_run` returns it. The tables a run wrote are
  in `results`, beside `summary` (was `summary.tables`); a list of runs leaves `results` out.
  `GET /runs/{run}` and `get_run` take `wait` (0 to 600 seconds). A run has `scheduled` (was
  `schedule`). Run logs take `limit` (was `tail`).
- **`incomplete`** is the status of a first copy that stopped at the time limit (was `partial`); the
  next run continues it. Runs that an earlier version stored keep `partial`.
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z` (were
  seconds since 1970). A schedule's `nextRunAt` is `null` while it is not enabled, and a schedule has
  `createdAt`.
- **REST bodies are camelCase:** `expectedRevision` (was `expected_revision`) and `fullRefresh` (was
  `full_refresh`). The tools keep `expected_revision` and `full_refresh`.
- **Delete** is `DELETE /replications/{replication}?confirmName=<name>` (was `POST /replications/{replication}/delete`
  with a JSON body) and returns `{"deleted": true, "replication": "<id>"}` (was `id`). `dropSlotSql`
  and `warning` come with it as before.
- **Secrets** are set with `PATCH /replications/{replication}/secrets` (was `PUT`): the call merges.
- **Environments.** `enable_environment` and `disable_environment` return the environment as
  `list_environments` shows it: `{team, environment, enabled, enabledBy, databases}`. Disabling one that
  is not enabled is 409 `environment_not_enabled` (was 404). The service account's id is no longer
  returned.
- **`whoami`** returns only what this extension shows about you; `sharedDatabases` is gone.
- **Errors.** A database in the body that is not the team's in that environment is 422
  `invalid_database` (was `unknown_database`). An unknown path is 404 `not_found`, a wrong method 405
  `method_not_allowed` and an unexpected error 500 `internal_error`, all as `{"error", "code"}`.
- `update_replication` and `set_replication_secrets` are marked destructive, so an agent asks first: they can
  remove secrets and the saved position in the change log.
- The OpenAPI contract describes every response and the error body.

Apache Polaris 1.9:

- A sync sets its table properties again when a concurrent commit got there first (409), instead of
  reporting the table under `published.errors`.

## 0.0.1 - 2026-11-19

- First version of Replication (OLake) for the Iceberg Data Platform, a **beta**, on Extension Bridge contract 0.1.
- PostgreSQL sources: change data capture through a replication slot and publication, incremental
  on a cursor column, or full copies, per table. The first run copies the existing rows (or only
  follows new changes), then each run reads the changes since the last one.
- Updates and deletes are written as positional deletes (Iceberg v2, the default) or deletion
  vectors (Iceberg v3), which DuckDB, PyIceberg and Polars all read. Equality deletes are never used.
- Source checks: OLake's prerequisites (logical WAL, replication privilege, slot, publication) and
  the tables it can replicate, with the SQL the database owner runs once.
- Runs execute OLake v1.2.0 in an isolated container as the team's service account, with a
  short-lived write token and vended storage credentials, and stop before the token expires; a large
  first copy continues in the next run. Every table shows **Produced by olake** in the platform
  catalog, with the change log position it was read up to.
- Sources on private networks run only inside the networks a platform administrator allows
  (`REP_ALLOWED_SOURCE_NETWORKS`). The source address is resolved and checked before every run and
  pinned in the run container.
- A page, a REST API and an MCP server with the same capabilities; the `replication` agent skill.
