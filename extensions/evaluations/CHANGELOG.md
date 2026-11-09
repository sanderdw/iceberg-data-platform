# Changelog

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
