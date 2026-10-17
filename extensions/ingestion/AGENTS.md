# Working on the Ingestion extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `extensions/ingestion`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`ingestion/bridge_client.py`) and standard Iceberg REST/S3.
- Every capability is a service method in `ingestion/services.py`, exposed as a REST operation
  (`ingestion/api.py`, listed in `OPERATIONS`) and an MCP tool (`ingestion/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/ingestion-api/v1/openapi.yaml` is
  stale: regenerate it with `uv run python -m scripts.api_contract`.
- Pipelines are configuration, never code. A new source option goes through `ingestion/validate.py`
  (an allowlist per source type) and is built in `runner/`; never pass user input to dlt unchecked.
- Secrets: definitions refer to them as `$secret:<name>`; values are AES-GCM encrypted
  (`ingestion/secrets.py`), write-only, passed to a run only as environment variables and scrubbed
  from logs. No response may contain one.
- Authorization lives in `Services`; runs only get `write` tokens of the team's automation
  principal from the Bridge.
- The launcher is the only component with the Docker socket: keep its inputs fixed (image, flags,
  mounts, networks). Runner code (`runner/`) runs inside the isolated image; its address guard
  refuses private addresses unless the platform administrator allows them.
- On Kubernetes (`LAUNCHER_BACKEND=kubernetes`, `launcher/kubernetes.py`) the launcher creates one Job
  per run in the runs namespace; it is the only container with a service account token. The runner
  (`runner/remote.py`) fetches its job and returns `out/` through the launcher's two job endpoints with
  a ticket valid for one run, and lifts the address guard only for that upload. `launcher/kube.py` is a
  copy of the platform's `server/kube.py`. Keep `deploy/helm/iceberg-ingestion/Chart.yaml` at the
  extension's version. `scripts/k8s_smoke.py` checks runs on a cluster.
- `scripts/e2e_smoke.py` checks a live installation through MCP. `DECISIONS.md` records why dlt is
  used the way it is.
