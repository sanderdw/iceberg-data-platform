# Working on the Replication extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `extensions/replication`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`replication/bridge_client.py`) and standard Iceberg REST/S3.
- Every capability is a service method in `replication/services.py`, exposed as a REST operation
  (`replication/api.py`, listed in `OPERATIONS`) and an MCP tool (`replication/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/replication-api/v1/openapi.yaml` is
  stale: regenerate it with `uv run python -m scripts.api_contract`.
- A tool returns exactly what its REST operation returns. Build every response with a projection in
  `Services` (`public`, `public_run`, `public_check`, `public_schedule`, `public_me`,
  `public_environment`), never from a stored row or a Bridge response as it is, and pass every time
  through `timestamp()`. Declare each key in the operation's response model in `replication/api.py`;
  a key the model does not declare is dropped from the REST response.
- REST is camelCase (`fullRefresh`, `expectedRevision`, `confirmName`); tool names and parameters
  are snake_case. A list is an object with one plural key. A resource is named by its noun
  (`replication`, `run`, `team`), never `id`. Errors are `ReplicationError` with a stable code.
- Run statuses are `queued`, `running`, `success`, `incomplete`, `failed`, `error` and `cancelled`.
  `incomplete` is a first copy that stopped at the time limit; the runner reports it and the next
  run continues.
- Replications are configuration, never code. A new source option goes through `replication/validate.py`
  (an allowlist per source type) and is built into OLake's files in `runner/olake.py`; never pass user
  input to OLake unchecked. Equality deletes are never offered (DECISIONS.md).
- Secrets: definitions refer to them as `$secret:<name>`; values are AES-GCM encrypted
  (`replication/secrets.py`), write-only, passed to a run only as environment variables and scrubbed
  from logs. No response may contain one.
- Authorization lives in `Services`; runs only get `write` tokens of the team's automation
  principal from the Bridge.
- The launcher is the only component with the Docker socket: keep its inputs fixed (image, flags,
  mounts, networks). The gateway resolves and checks the source host before each run
  (`validate.pin`); the launcher pins that address in the run, which never resolves it itself.
  Runner code (`runner/`) runs inside OLake's image with Python's standard library only.
- On Kubernetes (`LAUNCHER_BACKEND=kubernetes`, `launcher/kubernetes.py`) the launcher creates one Job
  per run in the runs namespace; it is the only container with a service account token. The runner
  (`runner/remote.py`) fetches its job and returns `out/` through the launcher's two job endpoints with
  a ticket valid for one run. `launcher/kube.py` is a
  copy of the platform's `server/kube.py`. Keep `deploy/helm/iceberg-replication/Chart.yaml` at the
  extension's version. There is no cluster smoke test yet: the launcher's Job spec is covered by
  `tests/test_launcher_kubernetes.py`.
- `runner/catalog_proxy.py` is a copy of the dbt runner's. `tests/test_runner.py` runs the start-up
  program against a fake OLake command line; `scripts/e2e_smoke.py` checks a live installation
  through MCP. `DECISIONS.md` records why OLake is used the way it is, and what was verified.
