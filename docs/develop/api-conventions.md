# API and MCP conventions

Every extension has a REST API under `/api/v1` and an MCP server, and all of them follow the rules on this page, so a person or an agent who has learned one extension can use the next. The core MCP servers (administration and user portal) follow the naming, confirmation and annotation rules, and the [Extension Bridge](extension-bridge.md) follows the casing, response, delete and error rules. The portals' own `/api` routes serve their pages and are not covered.

Follow these rules when you change an extension or write a new one. `extensions/ingestion` is the reference implementation: its `services.py`, `api.py` and `mcp_server.py` show each rule in code.

## Casing

| Where | Rule |
| --- | --- |
| REST request bodies, query parameters, path parameters and responses | camelCase |
| MCP tool names and tool parameters | snake_case |
| MCP tool results | The same object the REST operation returns |
| Values, such as statuses, modes and error codes | Not field names, so not converted |

A path parameter is named after its resource: `{project}`, `{pipeline}`, `{run}`, `{questionSet}`. Never `{id}`.

Document formats that are stored keep their own keys: question-set YAML, pipeline definitions, and the governed query spec, including its `order_by`.

## Responses

- **Projections only.** A function in `services.py` builds every response from the stored row. No stored column names, no internal columns such as watermarks or service principals, and no Bridge response passed through.
- **Types.** A yes or no is a boolean. Something absent is `null`, not an empty string.
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z`, produced by one `timestamp()` function per service. Storage keeps what it has.
- **Lists** are an object with one named plural key, such as `{"pipelines": [...]}`, in REST exactly as in MCP. A list that can grow without bound takes `limit`, with a stated default and maximum. A value outside the range is refused, not clamped.

## Verbs and status codes

| Action | Method | Status |
| --- | --- | --- |
| Create | `POST` on the collection | 201, with the resource |
| Update | `PATCH` on the resource; what is left out stays | 200, with the resource |
| Replace a whole singleton, such as a pipeline's schedule | `PUT` | 200 |
| Delete | `DELETE` on the resource | 200, `{"deleted": true, "<noun>": "<id>"}` |
| Start a run | `POST …/runs` | 202, with the run |
| Another action, such as validate, compile or cancel | `POST` | 200 |
| Enable an extension for a team environment | `POST /teams/{team}/environments/{environment}` | 200, whether or not it was on |

## Delete and confirmation

A delete needs the resource's exact current name when the resource is addressed by an opaque id and deleting it removes data, history, authored content or other people's access. Databases, data shares, dbt projects, pipelines, replications and question sets need it. `delete_semantic_model` also asks for the name, as a deliberate safeguard.

Pass it as `?confirmName=` in REST and `confirm_name` in MCP, never as a JSON body on `DELETE`. A wrong name is 422 `confirmation_mismatch`.

## Errors

Every response that is not 2xx has the body `{"error": "<readable text>", "code": "<stable code>", "detail": {...}}`, with `detail` only when there is one. That includes unknown paths (404 `not_found`), wrong methods (405 `method_not_allowed`) and unexpected exceptions (500 `internal_error`).

One code per situation, and one status per code:

| Situation | Code | Status |
| --- | --- | --- |
| The request fails validation | `invalid_request` | 422 |
| No, invalid or expired token or session | `unauthorized` | 401 |
| Not a member of the team named in the request | `not_a_member` | 403 |
| Member, but the role is too low | `forbidden_role` | 403 |
| The addressed resource does not exist, or is not in the caller's teams | `unknown_<noun>`, such as `unknown_run` | 404 |
| A field in the body names something that does not resolve | `invalid_<field>`, such as `invalid_database` | 422 |
| Name already used | `name_taken` | 409 |
| Changed since the caller read it | `revision_conflict` | 409 |
| Blocked because a run is queued or running | `run_in_progress` | 409 |
| Stop of a run that already finished | `run_finished` | 409 |
| The extension is not enabled for this team environment | `environment_not_enabled` | 409 |
| The confirmation name does not match | `confirmation_mismatch` | 422 |
| The cron is not valid, or runs too often | `invalid_cron` | 422 |
| The platform refused or failed | `platform_error` | 502 |
| The Bridge cannot be reached | `bridge_unavailable` | 503 |

A code that only one service needs is fine, as long as it has one status. Errors from the Bridge pass through unchanged, with their status and code; a Bridge answer without a code is 502 `platform_error`.

## Runs

dbt, Ingestion, Replication and Maintenance share one run contract.

| Status | Meaning |
| --- | --- |
| `queued`, `running` | Not finished |
| `success` | Finished and did all of its work |
| `incomplete` | Finished without error, with work left that the next run continues |
| `partial` | Finished; some items succeeded and some failed |
| `failed` | The tool ran and reported failure |
| `error` | The platform could not run it |
| `cancelled` | Stopped on request |

| Operation | REST | Result |
| --- | --- | --- |
| Start | `POST /<parent>/{id}/runs` | 202 and the run |
| List | `GET /<parent>/{id}/runs?limit=` | `{"runs": [...]}`, newest first, without `results` |
| Get | `GET /runs/{run}` | The run with `results` |
| Logs | `GET /runs/{run}/logs?limit=` | `{"run", "status", "lines"}`, the last `limit` lines |
| Stop | `POST /runs/{run}/cancel` | `{"run", "cancelling": true}`, or 409 `run_finished` |

Start and get take `wait`: seconds, 0 by default and at most 600. The call returns when the run is final or the time has passed, with the run as it is then.

A run has `id`, its parent under the parent's noun, `environment`, `status`, `triggeredBy`, `agent`, `scheduled`, `createdAt`, `startedAt`, `finishedAt`, `exitCode`, `summary`, `error` and, on start and get, `results`.

## Schedules

A schedule has `cron` (five fields, UTC), `enabled`, `nextRunAt`, `lastRun`, and `id` where a parent has several. The minimum interval is 5 minutes; Maintenance asks for an hour.

- **One per parent** (Ingestion, Replication, Maintenance): `PUT` and `DELETE /<parent>/{id}/schedule`, with the tools `set_schedule` and `delete_schedule`. The schedule is embedded in its parent.
- **Several per parent** (dbt, Evaluations): `GET` and `POST /<parent>/{id}/schedules`, `PATCH` and `DELETE /schedules/{schedule}`, with the tools `list_schedules`, `create_schedule`, `update_schedule` and `delete_schedule`.

## Identity and environments

`GET /me` and `whoami` return `user`, `memberships`, `databases`, `extension`, `origin`, `environments`, `enabledEnvironments`, `userPortalUrl` and `apps` in every extension, plus the extension's own extras.

`list_environments`, `enable_environment` and `disable_environment` work with one entry: `{team, environment, enabled, enabledBy, databases: [{id, name}]}`.

On the page, every extension enables and disables itself the same way, with the Settings page from [`contracts/bridge/v1/settings/`](https://github.com/sanderdw/iceberg-data-platform/tree/main/contracts/bridge/v1/settings), copied unchanged (Conversational BI ports it to React and keeps the stylesheet):

- A **Settings** link in the header, next to Apps, opens `#/settings`. Its first option is **Environments** (`#/settings/environments`, `?team=<team>` scrolls to one team): each of the caller's teams with its three environments, who enabled each and its databases. An extension adds its own options after it.
- Team administrators get **Enable** and **Disable**. Both ask first and say what the extension may then do, or what stops. Others read that a team administrator enables it.
- A page about one team environment where the extension is not enabled shows the shared notice: **Enable** for team administrators, a link to Settings for others. No extension writes its own wording or its own enable button.
- The header is the same in every extension and in both portals: its own navigation, then **Read-only** (when the person has the read role in each of their teams, and the extension has anything to change), **Settings**, **Apps** and the account menu from [`contracts/bridge/v1/account-menu/`](https://github.com/sanderdw/iceberg-data-platform/tree/main/contracts/bridge/v1/account-menu). Under the person's name it shows their teams and roles (`IcebergAccount.teams`), **Getting started** (the extension's documentation), the theme switch and **Sign out**.
- The Settings page says, for every setting, what it does when it is on, what turning it off stops and who can change it. It sets its own type and spacing, so it looks the same in every extension.
- Settings that only make sense where the extension is enabled, such as Evaluations' live evaluation, are reached from that environment under Environments. They are not a second way to enable it.

## MCP names

- **Tools** are `verb_noun`: `list_pipelines`, `get_run`, `run_project`. `update_` merges, `set_` replaces a singleton or writes write-only values such as secrets. `whoami` is the one name without a noun.
- **The same concept has the same name on every server**: `whoami`, `list_environments`, `list_runs`, `get_run`, `get_run_logs`, `cancel_run`, `list_semantic_models`, `describe_semantic_model`, `query_semantic_model`, `delete_share`.
- **A name never means two things.** `list_tables` is the user portal's catalog listing, so Maintenance has `list_table_health`. `get_pipeline` is an Ingestion pipeline, so dbt has `get_pipeline_graph`.
- **Parameters name the resource**: `project`, `pipeline`, `run`, `schedule`, `question_set`. Never a bare `id`.
- **Semantic models:** the name is `model` and the document is `definition`. An LLM is `llm_model`, never `model`.

## Annotations

| Hint | True when |
| --- | --- |
| `readOnlyHint` | The call changes nothing a caller can see afterwards, including run records |
| `destructiveHint` | One call can delete a resource, data, history or secrets, remove or narrow someone's access, or overwrite a document that is not kept as a version |
| `idempotentHint` | Repeating the call changes nothing further and hands out no new secret |
| `openWorldHint` | The call makes the platform connect to a system outside it, such as a source database or an LLM provider |

Creating something, changing a name or a cron, committing to a Git branch and starting a run that writes the tables it is defined to produce are not destructive. A Maintenance run that maintains is, because it expires snapshots and deletes files.

Agents skip confirmation based on these hints, so an understated hint is the mistake to avoid.

## The contract file

Each extension commits its OpenAPI document under `contracts/<name>-api/v1/openapi.yaml`. It declares the `Error` schema on its error responses and response models for the shared shapes: identity, environments, runs, logs, schedules, deletes and every list envelope. Regenerate it from the extension's directory with `uv run python -m scripts.api_contract`; a test in `tests/test_api.py` fails when it is out of date, and another checks that every operation has an MCP tool that returns the same object.
