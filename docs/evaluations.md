# Evaluations

Evaluations checks how well [Conversational BI](conversational-bi.md) answers the questions your team cares about, and whether a new LLM, prompt or semantic model makes it better or worse. It is an extension: install it next to Conversational BI.

A **question set** belongs to one team, environment and semantic model. Each case is a question with what a good answer is:

| Expected | Meaning | Passes when |
| --- | --- | --- |
| `query` | The governed query that answers it | One of the agent's queries returns the same rows (in order, when the query orders), with the expected metrics |
| `refusal` | An error the agent must explain, such as `fan_out` (the split would count flights twice) | The agent ran into that error and stated no figures |
| `clarify` | A vague question, such as "on-time rate per airport" (departure or arrival?) | The agent asked back instead of querying |
| `decline` | A question the model can't answer: no such data, a forecast, a request to break the rules | No query, no figures |

The right answer to a `query` case is computed again at every run, so a case stays right when your data changes. On every case a `grounded` check also makes sure that each figure in the answer comes from a query result, a definition or the question: no invented numbers. With an LLM judge configured (`JUDGE_MODEL`), a rubric adds a check on tone and clarity. Most checks are code, so runs are cheap and give the same verdict every time.

## Install

Add `evaluations` next to `conversationalbi`:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | \
  ICEBERG_EXTENSIONS=conversationalbi,evaluations sh
```

Open http://localhost:3012 and sign in. A team administrator enables Evaluations once per environment: under **Settings** › **Environments** (Settings is in the header, and Environments is its first option), through an AI agent (`enable_environment`) or with `POST /api/v1/teams/{team}/environments/{environment}`. Environments shows who enabled each environment, and **Disable** revokes the team's service account there again. Enable Conversational BI in the same environment too: a run asks Conversational BI for the whole team, with what every member of the team can read. On Kubernetes, register it as `extensions.evaluations.host` and install its chart `iceberg-evaluations` ([add extensions](install-kubernetes.md#add-extensions)).

For an LLM judge, put `JUDGE_MODEL` (a Pydantic AI model string) and its provider's key in `evaluations/llm.env` in the installation directory, then run `docker compose up -d` in `evaluations`. To compare LLMs, the platform administrator lists them in Conversational BI's `BI_EVAL_MODELS` (with their keys in its `llm.env`).

## Use it

1. **Write cases.** Create a question set on the page or with your AI agent, for a semantic model from **agent models**. The cases are YAML in the [pydantic-evals](https://ai.pydantic.dev/evals/) format. The [semantic-model skill](build-a-semantic-model.md) saves its interview questions as `<name>.questions.sql`; **Import questions** turns them into cases. The flights example (`examples/flights.yaml` in the extension) has 25 cases to start from.
2. **Run.** **Run now** asks Conversational BI every question and scores the answers. A run's page shows each answer, its checks and why one failed.
3. **Compare.** Pick two runs to see what got better or worse case by case, or let your AI agent run the set once per LLM (`compare_llm_models`).
4. **Schedule.** Team administrators run a set on a cron schedule in UTC, such as nightly after the data loads. A schedule runs at most every 5 minutes.

Every change to the cases is a new version, so a run always says which cases it used.

A run only counts when the agent actually answered. If more than 5% of the questions got no answer, such as under an LLM rate limit, the run is marked **Not reliable**: it has no pass rate, stays out of the result tables, and can't be compared. Otherwise the few unanswered questions are left out of the pass rate, and a comparison shows them as `no_answer`, never as broken.

## Results in your lakehouse

Set a **target database** on the question set and every run lands in the team's own database, in namespace `evaluations`. A run's `summary.published` says where its results went, or why they were not written:

- `case_results`: one row per question per run, with a column per check;
- `runs`: one row per run, with its pass rate, tokens and timing;
- the semantic model `evaluation_results`, so you ask Conversational BI "how did the pass rate develop per LLM?" like any other question.

The tables are ordinary Iceberg tables: read them in a notebook with DuckDB, share them, or build a dbt model on them.

## What the semantic model adds

A pass rate says how good the agent is, not how much of that comes from the semantic model. **Measure the semantic model** finds out: it asks the same questions of the same LLM twice. Once as always, and once with only the table and column names of the tables the model reads, writing its own SQL. Both are scored the same way, and the comparison shows both pass rates, the difference in points per expected outcome, and which questions the model fixed or broke.

Because an LLM writes SQL here, the platform administrator has to allow it: set `BI_EVAL_SCHEMA_ONLY=true` in Conversational BI's `.env` (on Kubernetes `evaluation.schemaOnly=true`). The chat never does this. The SQL is checked against an allowlist and runs read-only on the model's tables; see the [security model](security.md).

Read the number with one thing in mind: the right answer is the semantic model's own query. An answer that reasonably defines "on time" differently fails. That is partly the point, because agreed definitions are what a semantic model adds, but the number measures "matches this model's definitions" and so favours the semantic model. The split per expected outcome and the case-by-case list show where a difference comes from.

## Live evaluation

A team administrator switches on **Live evaluation** for an environment where Evaluations is enabled, from that environment under **Settings** › **Environments**. From then on Conversational BI keeps that team's conversations in that environment, for its retention period (30 days by default), and only Evaluations reads them. Enabling Evaluations alone keeps nothing, and switching live evaluation off deletes the conversations that were kept. Every check (hourly by default, at most every 5 minutes) scores a sample of the new ones:

- `grounded`: every figure comes from a result;
- `refusal_explained`: a refused query isn't answered around with figures;
- with a judge, whether the answer answers the question.

Conversations that fail a check wait in **To review**. Dismiss one when the answer was fine, or **Make a case** of it: the question becomes a regression case of a question set, by default with the query the agent ran. Check that query first. With a target database, the scores also go to `evaluations.live_scores`.

## Connect an AI agent

```bash
claude mcp add --transport http --client-id ext-evaluations-mcp --callback-port 3010 iceberg-evaluations http://localhost:3012/mcp
```

The `agent-evaluations` skill tells the agent how to write cases, run them and explain failures. Its tools:

| Tool | What it does |
| --- | --- |
| `whoami` | Your teams, roles and databases, and where Evaluations is enabled |
| `list_environments`, `enable_environment`, `disable_environment` | Where Evaluations is enabled for a team, with each environment's databases; enabling and disabling (team administrators) return the environment as `list_environments` shows it |
| `list_agent_models` | The semantic models the agent answers from (`semanticModels`), the LLMs a run may choose (`llmModels`) and whether the control arm is allowed (`schemaOnly`) |
| `list_question_sets`, `get_question_set` | Question sets with their last run; one with its cases, versions, schedules and latest runs |
| `create_question_set`, `update_question_set`, `import_questions` | Creates a question set, or changes one: an update changes only what you give, and new cases are a new version |
| `delete_question_set` | Deletes a question set with its versions, runs and schedules. Needs `confirm_name`, the question set's exact name |
| `run_evaluation`, `compare_llm_models`, `measure_semantic_model` | Start runs and return them: one run, one per LLM, or the pair with and without the semantic model |
| `list_runs`, `get_run`, `compare_runs` | A question set's runs, newest first (`limit`, 20 by default, at most 200); one run with every case; two runs case by case |
| `list_schedules`, `create_schedule`, `update_schedule`, `delete_schedule` | Schedules (team administrators). A question set can have several |
| `get_live_evaluation`, `update_live_evaluation` | Live evaluation of a team environment. An update changes only what you give; switching it off deletes the conversations that were kept |
| `list_reviews`, `update_review`, `promote_review` | The review queue (`limit`, 100 by default, at most 500); dismiss a review or put it back with `status` `dismissed` or `open`; turn one into a case |

Readers view everything of their teams. Writers edit question sets and run them in development, and delete them there. Team administrators work in acceptance and production, schedule runs and switch live evaluation on.

### What the tools and the API return

Every tool returns what its REST operation under `/api/v1` returns; the [OpenAPI contract](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/evaluations/contracts/evaluations-api/v1/openapi.yaml) describes each one.

- A list is an object with one key: `{"questionSets": [...]}`, `{"runs": [...]}`, `{"schedules": [...]}`, `{"reviews": [...]}`, `{"environments": [...]}`.
- Times are RFC 3339 strings in UTC, such as `2026-11-24T09:15:02.123Z`, or `null`.
- A run's `status` is `queued`, `running`, `success` (it finished) or `error` (it could not run; `error` says why). A run that finished without enough answers is `success` with `summary.reliable` false. `triggeredBy` is the person's name, or `schedule`; `agent` names the MCP client when an AI agent started it. `summary` holds the totals and `get_run` adds `cases`, one per question.
- An LLM is always `llmModel` (`llm_model` in a tool); `model` is a semantic model: `{"database", "namespace", "name"}`.
- A schedule's `nextRunAt` is `null` while it is not enabled.
- A delete returns `{"deleted": true, "questionSet": "<id>"}` or `{"deleted": true, "schedule": "<id>"}`. In REST the name to confirm is a query parameter: `DELETE /api/v1/question-sets/{questionSet}?confirmName=<name>`.
- An error is `{"error": "<text>", "code": "<code>"}`, sometimes with `detail`; a tool reports it as `<text> [<code>]`. REST request bodies and query parameters are camelCase (`llmModel`, `targetDatabase`, `confirmName`); tool parameters are snake_case.

## With OpenTelemetry

With [OpenTelemetry](observability.md) on, a run is one trace: a span per question, with Conversational BI's agent run and its LLM and tool calls nested under it. Every check is a `gen_ai.evaluation.result` event on the question's span, so Logfire, Langfuse or Grafana show scores next to what the agent did.

## Limits

- Evaluations asks Conversational BI only; other agents would need the same evaluation API.
- A conversation is recorded for live evaluation only when the agent selected a semantic model, because that ties it to one team.
- Each run costs LLM calls: one agent run per question, plus the judge when configured.
- Runs execute in the extension's process, at most `EVAL_MAX_RUNS` (2) at once, `EVAL_CONCURRENCY` (2) questions each.
