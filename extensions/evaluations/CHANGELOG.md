# Changelog

## Unreleased

The REST API and the MCP tools follow the platform's one set of conventions. Nothing of the old shapes
is kept: update scripts and agent instructions. It needs the Conversational BI release with the same
conventions in its evaluation API.

- **Tools renamed:** `save_question_set` is now `create_question_set` and `update_question_set`;
  `compare_models` is `compare_llm_models`; `set_schedule` is `create_schedule` and `update_schedule`,
  with the new `list_schedules`; `get_live` and `set_live` are `get_live_evaluation` and
  `update_live_evaluation`; `review_conversation` is `update_review` (with `status` `dismissed` or
  `open`, was `action`); `promote_to_case` is `promote_review`. 26 tools, were 23.
- **A question set is `question_set` in every tool** (was `id`), and a schedule is `schedule` (was
  `schedule_id`). Paths use `{questionSet}`.
- **REST paths and methods.** `POST /question-sets` only creates, and returns 201;
  `PATCH /question-sets/{questionSet}` changes one. `POST …/compare-llm-models` (was
  `compare-models`). `POST /question-sets/{questionSet}/schedules` creates a schedule (201, no
  `schedule_id` parameter); `PATCH /schedules/{schedule}` changes one and `DELETE /schedules/{schedule}`
  deletes it (was under the question set); `GET /question-sets/{questionSet}/schedules` lists them.
  `PATCH …/live` (was `PUT`) and `PATCH /reviews/{review}` (was `POST`). The operations that start runs
  return 202.
- **REST bodies and responses are camelCase:** `llmModel`, `llmModels`, `targetDatabase`,
  `questionsSql`, `samplePct`, `questionSet`, and in responses `createdAt`, `createdBy`, `updatedAt`,
  `updatedBy`, `triggeredBy`, `startedAt`, `finishedAt`, `nextRunAt`, `lastRun`, `toolCalls`,
  `inputTokens`, `outputTokens`, `durationMs`, `traceId`, `askedAt`, `reviewedBy`, `reviewedAt`,
  `promotedTo`, `lastChecked`, `lastSummary`. The tools keep snake_case parameters.
- **Changes merge.** `update_question_set`, `update_schedule` and `update_live_evaluation` change what
  you give and keep the rest. `set_live` used to put a setting you left out back to its default.
- **Runs.** A finished run has status `success` (was `done`). `triggeredBy` is the person's name, or
  `schedule`; `agent` names the MCP client when an agent started it; `scheduled` and `schedule` say
  whether and which schedule did. Where the results went is `summary.published` (was `published` on the
  run), and the LLMs that answered are `summary.llmModels` (was `summary.models`). A case result's LLM is
  `llmModel` (was `model`). `list_runs` takes `limit` (20 by default, at most 200; was always 50).
- **Comparing runs:** the counts are `change.noAnswer` (was `no_answer`); a case's `change` is still
  `no_answer`.
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z` (were
  seconds since 1970). A schedule's `nextRunAt` is `null` while it is not enabled.
- **Empty is `null`:** `targetDatabase`, `llmModel`, `error`, `pair`, `traceId`, `note`, `reviewedBy`
  and `promotedTo` are `null` when there is none (were empty texts).
- **Delete.** `delete_question_set` needs `confirm_name`, the question set's exact name
  (`DELETE /question-sets/{questionSet}?confirmName=<name>`), and returns
  `{"deleted": true, "questionSet": "<id>"}`. A run that is still going refuses it with 409
  `run_in_progress` (was `run_active`). Deleting a schedule returns `{"deleted": true, "schedule": "<id>"}`.
- **Schedules and live checks run at most every 5 minutes;** a tighter cron is 422 `invalid_cron`.
- **Environments.** `list_environments` returns `{"environments": [...]}` in REST too (was a bare
  array), each with the team's databases. `enable_environment` and `disable_environment` return the
  environment as `list_environments` shows it: `{team, environment, enabled, enabledBy, databases}`.
  Disabling one that is not enabled is 409 `environment_not_enabled` (was 404). The service account's id
  is no longer returned.
- **`whoami`** returns only what this extension shows about you; `sharedDatabases` is gone.
- **`list_agent_models`** returns `semanticModels` (was `models`).
- **Live evaluation** no longer shows where it stopped reading (`watermark`). `list_reviews` takes
  `limit` (100 by default, at most 500).
- **Errors.** An unknown path is 404 `not_found`, a wrong method 405 `method_not_allowed` and an
  unexpected error 500 `internal_error`, all as `{"error", "code"}`. A run, schedule or review of
  another team is 404 `unknown_run`, `unknown_schedule` or `unknown_review`. Creating a question set in
  a team you are not in is 403 `not_a_member`. Renaming a question set to a name that is taken is 409
  `name_taken` (was an unexpected error).
- `run_evaluation`, `compare_llm_models` and `measure_semantic_model` are marked as reaching outside the
  platform (an LLM provider). `update_live_evaluation` is marked destructive, so an agent asks first:
  switching it off deletes the conversations Conversational BI kept.
- Disabling Evaluations for an environment checks that you administer the team before it ends live
  evaluation there.
- **Conversational BI's evaluation API:** this extension sends `llmModel` and an RFC 3339 `since`, and
  reads `semanticModels`, `llmModel`, `toolCalls`, `inputTokens`, `outputTokens` and `traceId`. It never
  receives who asked a question.
- The OpenAPI contract describes every response and the error body.

## 0.1.1 - 2026-11-18

- Updated dependencies, including FastAPI 0.162.4, croniter 7.0.0 and uv 0.13.8.

## 0.1.0 - 2026-11-09

- **Question sets with known answers** for Conversational BI, as pydantic-evals YAML with a version
  history, written in the page or through MCP, or imported from the semantic-model skill's
  `<name>.questions.sql`. A case expects a governed query, a refusal, a question back or a polite no.
- **Scored runs:** result_matches, right_metrics, grounded, refused, clarified and declined in
  code, and an optional LLM judge. Runs on demand, on a schedule, or once per LLM to compare models;
  any two runs compared case by case.
- **What the semantic model adds:** one run with it and one where the same LLM has only the table schemas,
  compared on the same checks. Needs `BI_EVAL_SCHEMA_ONLY=true` in Conversational BI.
- **A health gate:** a run where more than 5% of the questions got no answer (rate limits, an outage) is
  not reliable: no pass rate, no comparison, nothing in the result tables.
- **Results in the team's lakehouse:** `evaluations.runs`, `evaluations.case_results` and
  `evaluations.live_scores`, with the semantic model `evaluation_results`.
- **Live evaluation:** a sample of real conversations scored on a schedule, for teams that opt in;
  failures go to a review queue and become regression cases.
- OpenTelemetry: a span per run and per question, with Conversational BI's agent run nested under
  it, and every score as a `gen_ai.evaluation.result` event.
- Needs the platform with Bridge contract 0.1.3 and Conversational BI with the evaluation API.
