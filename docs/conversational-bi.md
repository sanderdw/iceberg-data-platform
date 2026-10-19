# Conversational BI

Ask questions about your data in a chat. Answers come from **governed queries** on Apache Ossie [semantic models](semantic-models.md): your teams' own models, and the models that other teams **share with your team** through [data shares](data-shares.md). The model owner decides what a metric means, how datasets join and which fields exist; the agent only picks names. Every answer shows the query it ran, the join path and the metric definitions, and charts load their numbers from the result, never from the LLM.

| | |
| --- | --- |
| Chat | `http://localhost:3007` |
| LLM | Google Gemini, Anthropic, OpenAI or Amazon Bedrock, set with `LLM_MODEL` |
| API | `http://localhost:3007/api/v1` |
| MCP | `http://localhost:3007/mcp` (Keycloak sign-in through the `ext-conversationalbi-mcp` client) |

## Install

The installer asks whether to add Conversational BI, then which model to use and its key. At the prompt the key is never echoed and stays out of your shell history. To install without questions, pass the model and its key in the environment:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | \
  LLM_MODEL=anthropic:claude-sonnet-5-5 ANTHROPIC_API_KEY=sk-ant-... sh
```

```powershell
$env:LLM_MODEL = 'anthropic:claude-sonnet-5-5'; $env:ANTHROPIC_API_KEY = 'sk-ant-...'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

`ICEBERG_EXTENSIONS=conversationalbi` adds it without a model, and `ICEBERG_EXTENSIONS=none` skips the question. Allocate 8 GiB of Docker memory with it. Rerunning the installer keeps Conversational BI and `llm.env`. Pass `LLM_MODEL` and its key again to replace them.

A **team administrator** enables Conversational BI once per environment: the **Enable** button in the chat's sidebar, or `enable_environment` over MCP. That creates the team's read-only service account for that environment, which also reads the data shares the team received there.

On Kubernetes, install its chart next to the platform's; see [Install on Kubernetes](install-kubernetes.md#add-extensions).

## Connect an AI agent

```bash
claude mcp add --transport http --client-id ext-conversationalbi-mcp --callback-port 3010 \
  iceberg-conversationalbi http://localhost:3007/mcp
```

Other agents are set up as for the [user portal](ai-agents.md), with this address and client ID.

## What it can read

| Model | You can ask about it when |
| --- | --- |
| In a database of one of your teams | you are a member of that team, in any role |
| Shared with one of your teams | one of your teams received the model **and every table it reads**, and enabled Conversational BI for that environment; that team's service account reads it |

Everything is re-checked on every question and when a chart loads its rows: a revoked share, a table removed from it, an expired share or a removed membership ends access within 30 seconds. Data is read with **read-only** tokens of the team's service account. Conversational BI never writes to the platform.

## Governed queries

The `query` tool (and `POST /api/v1/queries`) takes names, not SQL:

```json
{"metrics": ["on_time_arrival_pct"],
 "dimensions": [{"field": "FLIGHT.date", "grain": "week"},
                {"field": "AIRPORT.code", "via": ["route_departure_airport"]}],
 "filters": [{"field": "FLIGHT.date", "op": "between", "value": ["2026-01-01", "2026-01-14"]}],
 "limit": 500}
```

- A metric belongs to the dataset its SQL reads (its *home*). Metrics of different homes go in separate queries.
- Joins follow relationships from the home, in their direction, and only onto the target's whole primary key. They can never multiply rows: a dimension that would (such as `RUNWAY.length` on flight metrics) is refused with `fan_out`.
- When a dataset is reachable in several ways, the shortest unique path wins; otherwise the query is refused with `ambiguous_join` and the options, and `via` picks one.
- Metric and field SQL from the model passes an allowlist of scalar and aggregate expressions: no queries, table functions, file readers or unknown functions. Values are always parameters.
- The worker binds exactly the tables it needs with `iceberg_scan` over the metadata Polaris returned, then disables local files and locks its configuration. It runs with a scrubbed environment, a 30-second limit and memory limits.

Every result also carries:

- `recommended`: the component that shows it best, decided from its columns rather than by the LLM. One metric without dimensions is a KPI, a time dimension a line, categories are bars (sideways for many or long names), and two category dimensions split the bars into series, stacked only when the metric adds up (a `SUM` or `COUNT`, never an average). Anything wider is a table. The agent follows it unless you ask for another view.
- `dataAsOf`: when the metrics' dataset was last committed, from the Iceberg snapshot the query read. Query cards and charts show it as *data as of*.

## What goes to the LLM

The LLM provider receives the conversation, the agent's instructions, the selected model's names, descriptions and owner guidance, the compiled SQL, the metric definitions, and **at most 20 rows of each result** (`BI_LLM_ROWS`, capped at 8 KB). `BI_LLM_ROWS=0` sends no rows: the agent still charts results, because charts and tables load their rows from the gateway. It never receives tokens, credentials or rows beyond that sample. The owner's guidance is passed as quoted data, never as instructions, and tools can only read what the person could read anyway.

## Change the model

The model and key are in `conversationalbi/llm.env` in the installation directory, readable only by you and given only to the chat's gateway container. `LLM_MODEL` is a [Pydantic AI model string](https://pydantic.dev/docs/ai/models/overview); its provider needs:

| Provider | `LLM_MODEL` | Variables |
| --- | --- | --- |
| Google Gemini | `google:gemini-3.8-flash` (the default) | `GOOGLE_API_KEY` |
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai:gpt-5.5`, `openai-chat:…` | `OPENAI_API_KEY`; `OPENAI_BASE_URL` for a compatible endpoint |
| Amazon Bedrock | `bedrock:global.anthropic.claude-sonnet-5-5`, `bedrock-mantle:…` | `AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` |
| Claude on Bedrock, Anthropic client | `anthropic-bedrock:global.anthropic.claude-sonnet-5-5` | as Bedrock |
| OpenRouter | `openrouter:anthropic/claude-sonnet-5.5`, with any model name from [openrouter.ai/models](https://openrouter.ai/models) | `OPENROUTER_API_KEY` |

To keep data on your own hardware, point `OPENAI_BASE_URL` at a local OpenAI-compatible server. After a change, restart the gateway from the installation directory:

```bash
docker compose -f conversationalbi/compose.yaml up -d --wait
```

On Kubernetes the model and key are in the Secret the chart's `llm.existingSecret` names (or the chart's own `llm.model` and `llm.env`). Replace the Secret with the new `LLM_MODEL` and key, then restart the gateway with `kubectl -n iceberg rollout restart deploy/cbi-gateway`. The row limits are `query.llmRows` and `query.maxRows` there; see [add extensions](install-kubernetes.md#add-extensions).

A model that cannot start (unknown, or without its key) does not stop the extension: `/api/health` reports it under `llm`, the chat answers with what is missing, and the API and MCP server keep working.

## Limits

Conversations live in the runtime's memory and end when it restarts. Results are kept for 30 minutes. One query reads at most 5,000 rows; `BI_MAX_ROWS` can lower that.

How it is built: [Conversational BI internals](develop/conversational-bi.md).
