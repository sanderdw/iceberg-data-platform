# Changelog

## 0.1.0 - 2026-10-01

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
