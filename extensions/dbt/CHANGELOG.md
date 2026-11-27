# Changelog

## Unreleased

The REST API and the MCP tools follow the platform's one set of conventions. Nothing of the old shapes
is kept: update scripts and agent instructions.

- **Bridge contract 1.0.** Needs the platform 1.0.0 or later (Bridge contract `>=1.0,<2`); it no longer
  starts against a 0.9 platform. A Bridge error keeps its status and code (a 503 or 500 was 502).
- **Account menu.** The header ends the same way as in the administration and user portals: **Read-only** when
  you have the read role in each of your teams, then Settings, Apps and your name. Your name opens your teams and
  roles, **Getting started** (this extension's documentation), the theme switch and **Sign out**. Settings says what
  turning it on and off does and who can change it, and looks the same in every extension.
- **Settings.** Where dbt is enabled moved from a project's settings tab to **Settings** › **Environments**
  in the header, the page every extension shares: it covers all your teams, not one project's, with Enable and
  Disable for team administrators. The project tab is now called **Project settings**. A project in an environment where dbt is off says so, with Enable for
  team administrators.
- **Apps menu.** Drawing the menu again no longer leaves the previous menu's listeners on the page.
- **Every response is described.** Files, commits, branches, diffs, merges, compiling, previews, the
  pipeline graph, nodes and lineage have their own schema in `contracts/dbt-api/v1/openapi.yaml`. A
  diff's `added` and `removed` are numbers, null for a binary file (were strings, `-` for binary).
- **Tools renamed:** `run` is `run_project`, `compile` is `compile_project`, `diff` is `get_diff`,
  `get_pipeline` is `get_pipeline_graph` and `get_model` is `get_node`. `set_schedule` is split into
  `create_schedule` and `update_schedule`; an update changes only what you give.
- **Lists** are an object with one key, in REST as in MCP: `{"projects": [...]}`, `{"branches": [...]}`,
  `{"commits": [...]}`, `{"runs": [...]}`, `{"schedules": [...]}` and `{"environments": [...]}` (REST
  returned bare arrays).
- **Runs.** Starting a run returns 202 with the run as `get_run` returns it, with `results`; the body
  of `POST /projects/{project}/runs` is optional. `run_project` no longer waits by default: pass
  `wait` (0 to 600 seconds), there or on `get_run`. A run has `triggeredBy`, `createdAt`, `startedAt`,
  `finishedAt`, `exitCode` and `select` (were `triggered_by`, `created_at`, `started_at`,
  `finished_at`, `exit_code` and `selector`), and `scheduled` beside `schedule`; a scheduled run's
  `triggeredBy` is `schedule`. `access` and the service account's id are no longer returned. A list
  of runs returns 20 by default (was 30). Run logs take `limit` (was `tail`; 500 lines by default,
  was 400) and return the run's `status`. Stopping a run returns `{"run", "cancelling": true}`; a
  run that already finished is 409 `run_finished`.
- **Schedules** have `select`, `nextRunAt`, `lastRun`, `createdBy` and `createdAt` (were `selector`,
  `next_run_at`, `last_run`, `created_by` and `created_at`), and `enabled` is a boolean. A schedule
  is changed with `PATCH /schedules/{schedule}` (was `PUT /projects/{project}/schedules/{schedule}`,
  which replaced it). A schedule runs at most every 5 minutes; a tighter cron is 422 `invalid_cron`.
- **Projects** have `defaultDatabase`, `createdAt` and `createdBy` (were `default_database`,
  `created_at` and `created_by`).
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z` (were
  seconds since 1970), also a commit's `committedAt` and a node's `lastRunAt`. A schedule's
  `nextRunAt` is `null` while it is not enabled.
- **REST bodies are camelCase:** `defaultDatabase`, `expectedRevision` and `fullRefresh` (were
  `default_database`, `expected_revision` and `full_refresh`). The tools keep the snake_case names.
- **Delete** is `DELETE /projects/{project}?confirmName=<name>&force=true` (was a JSON body with
  `confirm_name` and `force`).
- **Environments.** `enable_environment` and `disable_environment` return the environment as
  `list_environments` shows it: `{team, environment, enabled, enabledBy, databases}`, with each
  database as `{id, name}` (were names). Disabling one that is not enabled is 409
  `environment_not_enabled` (was 404). The service account's id is no longer returned.
- **`whoami`** returns only what this extension shows about you; `sharedDatabases` is gone.
- **Errors.** `name_taken` replaces `project_exists` and `branch_exists`, `revision_conflict` replaces
  `stale_branch`, `run_in_progress` replaces `run_active`, `confirmation_mismatch` replaces
  `confirmation_required`, and a default database that is not the team's is 422 `invalid_database`
  (was `unknown_database`). A run or schedule of another team is 404 `unknown_run` or
  `unknown_schedule` (was `unknown_project`). Deleting `main` is 403 `protected_branch` (was 422). A
  `limit`, `wait` or `depth` outside its range is refused, with 422 `invalid_request` in REST (the
  tools clamped it silently). An unknown path is 404 `not_found`, a wrong method 405 `method_not_allowed` and
  an unexpected error 500 `internal_error`, all as `{"error", "code"}`.
- `compile_project` and `preview_model` are no longer marked read-only, because each is recorded as a
  run. `cancel_run` is no longer marked destructive: it deletes nothing.
- `iceberg_dbt.py` and the viewer use the new API. Download `iceberg_dbt.py` again from the viewer; an
  older copy no longer works.
- The OpenAPI contract describes the shared responses and the error body.

Apache Polaris 1.9:

- A build sets its table properties and column docs again when a concurrent commit got there first
  (409), instead of reporting the table under `published.errors`.
- **Apache Ossie 0.3.0.dev0.** Files in `semantic_models/` are [Ossie 0.3.0.dev0](https://github.com/apache/ossie/blob/main/core-spec/spec.md)
  documents: `version: 0.3.0.dev0` and the model's keys at the root, expressions as `{sql: ...}`, and each
  dataset's `source` as `namespace.table` (Polaris refuses `lakehouse.namespace.table`). A file with a
  `semantic_model` list or another version is reported and not published.

## 0.1.4 - 2026-11-18

- Updated dependencies, including FastAPI 0.162.4, croniter 7.0.0 and uv 0.13.8.

## 0.1.3 - 2026-11-09

- **OpenTelemetry.** With `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env` (`otel.endpoint` on Kubernetes) the service sends its logs and traces over OTLP/HTTP, as the platform does. Off by default. See [Send logs and traces to OpenTelemetry](https://sanderdw.github.io/iceberg-data-platform/observability/).
- dbt v2 OSS 2.0.11 and updated dependencies.

## 0.1.2 - 2026-10-27

- DuckDB 2.0, the same version as the platform.
- certifi 2026.10.23 removes the distrusted eMudhra emSign roots from the Python HTTP clients.
- Updated dependencies.

## 0.1.1 - 2026-10-17

- **Runs on Kubernetes.** The chart `deploy/helm/iceberg-dbt` installs dbt next to the platform's chart.
  Each run is a Kubernetes Job in a namespace of its own, with the same isolation as a Docker run: the
  runner fetches its staged project and returns its results through the launcher with a ticket valid
  for one run (`LAUNCHER_BACKEND=kubernetes`). The Compose installation is unchanged.
- Acceptance and production builds and the docs site take only revisions that were the head of `main`:
  merging always adds a merge commit, so a commit hidden inside a merged branch is never promoted. The
  docs site, which runs scripts with the viewer's session, comes only from a successful docs run of such
  a revision; other runs keep no `out/docs`.
- `merge_to_main` takes the reviewed branch head as `revision` and refuses the merge if the branch moved.
- `iceberg_dbt.py` refuses any file path from the project that would leave the folder (a drive letter such as
  `C:/` on Windows, an absolute path or `..`) before writing anything, and the server refuses `:` in file paths.
- dbt v2 OSS 2.0.8.

## 0.1.0 - 2026-10-13

- First version of the dbt extension for the Iceberg Data Platform, on Extension Bridge contract 0.1.
- dbt v2 OSS 2.0.5 with DuckDB 1.5.5 on Iceberg REST, installed without run-time downloads.
- Git-backed projects per team with protected `main`, branches, commits, diffs and merges. A new
  project starts empty, with only `dbt_project.yml`, a README and the namespace macro.
- Runs and schedules as the team's automation principal per environment, in isolated containers
  with short-lived read or write tokens. Acceptance and production build only `main`. A run ends
  15 minutes before its token expires, so runs grow with the platform's `POLARIS_TOKEN_LIFETIME`.
- A fair run queue: at most `MAX_CONCURRENT_RUNS` runs at once and `MAX_RUNS_PER_TEAM` per team,
  and a build waiting for its project holds no slot. Logs and files of older runs are removed after
  `RUN_RETENTION` runs per project and environment.
- Runs reach storage when the platform's `S3_ENDPOINT` is a public HTTPS address, such as a quick
  share: a catalog proxy in the run points vended storage at the internal endpoint.
- `replace_table` materialization that keeps Iceberg table identity, data shares and docs.
- Producer, lineage and quality table properties and column docs after every build.
- Semantic models as code: Apache Ossie documents at `semantic_models/<database>/<namespace>/<name>.yml`
  are published to the run's environment after every successful build or run, and reported in the
  run's summary. The file wins over edits made elsewhere; an unchanged model keeps its version.
- `whoami` lists where dbt is enabled per team (`enabledEnvironments`), and a new project says when
  development still has to be enabled before it can build.
- Development is one shared sandbox per team. A branch build says it replaces the tables `main`
  builds, a branch's pipeline says when another branch built those tables since, and a merge says
  when `main` still needs a build before the viewer shows it.
- REST API and MCP server with the same capabilities; pipeline graph and lineage from dbt's
  Parquet information schema.
- Pipeline viewer, full window width: pick a branch to see its pipeline, node statuses and runs
  (`main` by default; `ref` in the API and MCP tools). Filter the graph by status and node type,
  focus it with dbt's `+name+` operators, preview a model's rows and build a node with its upstream
  or downstream from its panel. Sources show no status dot: dbt reads them but never builds them
  (`external` in the API). With nothing drawn on a branch, the viewer offers the branches that have a
  pipeline. Statuses and the run list follow running builds live, and a writer
  stops a run in any phase (it ends `cancelled`). The timeline leaves out previews.
- Files view: every project file at a branch, YAML, seeds and tests included. Writers edit, add
  and delete files on a branch; `main` is read-only there.
- Schedules are created, paused and deleted in the viewer; Settings enables or disables dbt per
  environment (team administrators) and lists and deletes branches. Writers edit a node's file from its panel,
  save it to a branch and build that branch in development; team administrators merge it to `main`.
- `iceberg_dbt.py`, downloaded from the viewer: dbt's commands (`build`, `run`, `test`, `seed`,
  `show`, `compile`, `parse`, `docs`) in your own terminal, run on the platform with the log
  streamed back. Commands run `main`; `clone`, `pull`, `status`, `push --branch` and `--ref` bring
  local edits along through a branch. The local dbt MCP server can use it as `DBT_PATH`.
- Writers create a project in the viewer. Team administrators delete one there or through the API
  and MCP: deleting forgets the project as if it never existed, including the dbt producer, lineage
  and quality properties on the tables it built. Tables, their data and docs stay.
