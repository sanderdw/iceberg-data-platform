# Changelog

## 0.1.3 - 2026-10-16

- KaTeX 0.18.11 in the web app, which fixes a prototype-pollution advisory (GHSA-238p-pmpm-9mq7) in
  the math rendering of chat answers.
- A semantic model's declared field type becomes a cast only when it is a known type, so a shared model
  can no longer add SQL to a governed query through it.
- Filters on derived fields that combine conditions with `AND` or `OR` keep their meaning: the field's
  expression is parenthesised wherever a query uses it.

## 0.1.2 - 2026-10-14

- Pydantic AI 2.56.0, which adds Gemini 4 Argon: set `LLM_MODEL=google:gemini-4-argon`. It also
  lifts the Google timeout to 20 minutes, so long answers are not cut off.

## 0.1.1 - 2026-10-13

- Updated dependencies, including FastAPI 0.143.0, Pydantic AI 2.55.0, the MCP SDK 2.3.0,
  CopilotKit 1.79.0, Python 3.14.8 and uv 0.13.0.
- A smaller gateway image, 0.9 instead of 1.3 GB: uv's download cache no longer ships inside it.
- The README and the skill register the MCP server as `iceberg-conversationalbi`, the name the
  platform's installer and `AGENTS.md` use.

## 0.1.0 - 2026-10-04

- First version of Conversational BI for the Iceberg Data Platform, on Extension Bridge contract 0.1.
- Chat with CopilotKit v2 generative UI: query cards, Chart.js charts, tables and KPIs that load
  their numbers from the query result by id. Each result recommends its chart (line, bars,
  horizontal or stacked bars, KPI or table) from its columns, and shows when its data was last
  committed. Answers are short and in the language of the question.
- A Pydantic AI agent defined in YAML (`analyst.yaml`), served over AG-UI. Choose OpenAI,
  Anthropic, Google Gemini (the default) or Amazon Bedrock with `LLM_MODEL`. Setup asks for the
  model and its key, or takes them from the environment, and stores them in `llm.env`, which only
  the gateway reads. A model that cannot start leaves the chat explaining what is missing, while
  the API and MCP server keep working.
- Governed queries compiled from Apache Ossie semantic models: metrics by dimensions, time grains
  and typed filters, fan-out-safe joins and `via` for ambiguous paths. It reads Ossie's dimension
  markers and datatypes, so derived date and timestamp fields take a time grain, and passes the
  synonyms of the model, its fields and metrics to the agent.
- Your teams' models and models shared with your teams (Bridge capability `shared-data`).
- Read-only: queries run in disposable DuckDB processes with one read token of the team's
  automation principal.
- REST API and MCP server with the same capabilities.
