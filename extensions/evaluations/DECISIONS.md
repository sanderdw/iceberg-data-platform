# Decisions for the Evaluations extension

## Open source only: pydantic-evals, no evaluation server

pydantic-evals (MIT) runs the cases and scorers in this process. Evaluation servers are not
bundled: Arize Phoenix is under the Elastic License 2.0, and Langfuse, Opik and MLflow each bring
their own databases (ClickHouse, MySQL, Redis) next to the lakehouse. Results are Iceberg tables in
the team's database instead, and the OpenTelemetry export reaches any of those servers for whoever
runs one: Langfuse and Logfire read OTLP directly.

## The right answer is a governed query, not a number

A case stores the query that answers its question. Every run computes that query again next to the
agent's answer, so a case stays right when the data is generated or loaded again, and the scorer
compares rows, not prose. Refusals (`fan_out`, `ambiguous_join`), questions back and polite noes
are outcomes too. An LLM judge only reads tone and clarity, and only with `JUDGE_MODEL`.

## Asking Conversational BI for a team: Bridge capability team-extensions (contract 0.1.3)

Extensions never call each other directly as a person, and the platform has no token exchange. The
Evaluations extension calls Conversational BI's evaluation API with its own Keycloak service account.
Conversational BI accepts that token only on `/api/v1/evaluation/*`, only for callers it lists
(`BI_AGENT_CALLERS`), and only after the Bridge confirms that the team enabled both extensions in
that environment. It then reads with its own automation principal: the scope every team member has,
without the person's narrowing. A team administrator enabling both is the consent.

## Live evaluation is opt-in per team

Conversational BI records conversations only for a team and environment where live evaluation is
switched on, keeps them 30 days (`BI_CONVERSATION_RETENTION_DAYS`) and gives them only to the
Evaluations extension, for that team; switching it off deletes them. A conversation is recorded only
when the agent selected a model, because that ties it to one team, and never with the person who
asked: a review is about the answer. The live check scores a fixed sample with the checks that need
no expected answer; failures wait for a person, who dismisses them or turns them into cases.

## One API shape with the platform's other extensions, over unchanged storage

Responses follow the conventions of dbt, Ingestion, Replication and Maintenance: camelCase, lists as
an object with one key, RFC 3339 times, `confirmName` to delete, the same environment and error
shapes. Every response is built by a projection in `services.py`; SQLite keeps its columns and
values, so nothing is migrated. That is why a finished run is stored as `done` and shown as
`success`, and why who started a run is stored as one text and shown as `triggeredBy`, `agent`,
`scheduled` and `schedule`.

Three things differ from the run contract of the other extensions, on purpose:

- A run has no `wait` and no cancel. It runs in this process and ends by itself; both would be new
  capability, not a new shape.
- A run's status is `queued`, `running`, `success` or `error`. There is no `failed`: questions the
  agent answered badly are the result of a run, not its failure. A run that got too few answers is
  `success` with `summary.reliable` false.
- The answers per question are `cases`, not `results`: a case is what a question set is made of.

Where the results went (`sales.evaluations.case_results`, or why they were not written) is
`summary.published`; the run has no `published` of its own.

A writer may delete a question set in development, as a writer may create and edit one there;
acceptance and production need a team administrator. The delete asks for the name in both.

## No runner, no Docker socket

Cases are data and the scorers are built in, so nothing untrusted runs: no launcher, no run
containers, one service.
