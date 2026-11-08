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

Conversational BI records conversations only for a team and environment where Evaluations is
enabled, keeps them 30 days (`BI_CONVERSATION_RETENTION_DAYS`) and gives them only to the Evaluations
extension, for that team. A conversation is recorded only when the agent selected a model, because
that ties it to one team. The live check scores a fixed sample with the checks that need no expected
answer; failures wait for a person, who dismisses them or turns them into cases.

## No runner, no Docker socket

Cases are data and the scorers are built in, so nothing untrusted runs: no launcher, no run
containers, one service.
