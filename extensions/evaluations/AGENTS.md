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
- A tool returns exactly what its REST operation returns. Build every response with a projection in
  `Services` (`public_set`, `public_run`, `public_case`, `public_schedule`, `public_live`,
  `public_review`, `public_me`, `public_environment`), never from a stored row or from the Bridge's
  or Conversational BI's answer as it is, and pass every time through `timestamp()`. Declare each
  key in the operation's response model in `evaluations/api.py`; a key the model does not declare is
  dropped from the REST response, and then the tool and the operation differ.
- REST is camelCase (`llmModel`, `targetDatabase`, `confirmName`); tool names and parameters are
  snake_case. A list is an object with one plural key. A resource is named by its noun
  (`question_set`, `run`, `schedule`, `review`), never `id`. An LLM is `llm_model` / `llmModel`;
  `model` always means a semantic model `{database, namespace, name}`. Errors are `EvalError` with
  a stable code.
- Storage keeps its own names and values (`runs.status` is `done`, times are seconds, a case's LLM
  is in `case_results.model`): the projections translate, so a rename never needs a migration.
- `evaluations/agent_client.py` is the client side of Conversational BI's evaluation API, and
  `FakeAgent` in `tests/conftest.py` the fake of it. `tests/test_agent_client.py` holds both against
  Conversational BI's contract file: change them together with `extensions/conversationalbi`.
- Authorization lives in `Services`; runs ask the agent for the team, never as the person who
  started them. Results go to Iceberg with the extension's own automation principal (`lakehouse.py`).
- Scoring is code first (`scorers.py`): add an LLM judge only for what code can't check. A scorer
  that does not apply to a case returns `{}`.
- Question sets are pydantic-evals YAML (`questions.py`); keep `Dataset.from_text` able to read them.
- `examples/flights.yaml` is checked against the flights model; `scripts/e2e_smoke.py` checks a live
  installation. Keep `deploy/helm/iceberg-evaluations/Chart.yaml` at the extension's version.
