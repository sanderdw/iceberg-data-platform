# Working on Conversational BI

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `extensions/conversationalbi`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`conversationalbi/bridge_client.py`) and standard Iceberg REST/S3.
- Every capability is a service method in `conversationalbi/services.py`, exposed as a REST
  operation (`api.py`, listed in `OPERATIONS`) and an MCP tool (`mcp_server.py`); the chat agent
  reaches the same methods through the `SemanticLayer` capability (`agent/capability.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/conversationalbi-api/v1/openapi.yaml`
  is stale: regenerate it with `uv run python -m scripts.api_contract`.
- Authorization lives in `Services` and is re-checked on every call from the Bridge's `/me`. Data
  is only read with read tokens; never ask the Bridge for `write`.
- The LLM never writes SQL and never supplies numbers to the UI. Query shapes are `QuerySpec`
  (`semantic/compiler.py`); expressions from a model pass `semantic/expressions.py`.
- The agent is `conversationalbi/agent/analyst.yaml`. Keep dynamic context in the capability's
  instructions, never in the YAML, and pass model owner text as quoted data.
- The chat UI is `web/` (React, CopilotKit v2, Chart.js) and follows the Nothing design system
  (github.com/dominikmartn/nothing-design-skill). Generative components bind to result ids.
