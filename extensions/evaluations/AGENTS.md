# Working on the Evaluations extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, image and
  version. Run everything from `extensions/evaluations`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- Never import from the platform's `server` or `user_portal` packages, nor from another extension.
  Talk to the platform only through the Extension Bridge (`evaluations/bridge_client.py`) and to
  Conversational BI only through its evaluation API (`evaluations/agent_client.py`).
- Every capability is a service method in `evaluations/services.py`, exposed as a REST operation
  (`evaluations/api.py`, listed in `OPERATIONS`) and an MCP tool (`evaluations/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/evaluations-api/v1/openapi.yaml` is
  stale: regenerate it with `uv run python -m scripts.api_contract`.
- Authorization lives in `Services`; runs ask the agent for the team, never as the person who
  started them. Results go to Iceberg with the extension's own automation principal (`lakehouse.py`).
- Scoring is code first (`scorers.py`): add an LLM judge only for what code can't check. A scorer
  that does not apply to a case returns `{}`.
- Question sets are pydantic-evals YAML (`questions.py`); keep `Dataset.from_text` able to read them.
- `examples/flights.yaml` is checked against the flights model; `scripts/e2e_smoke.py` checks a live
  installation. Keep `deploy/helm/iceberg-evaluations/Chart.yaml` at the extension's version.
