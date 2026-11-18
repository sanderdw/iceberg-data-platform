# Working on the Maintenance extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `extensions/maintenance`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- The runner (`runner/`) is a Go module of its own. Without Go installed, use a container:
  `docker run --rm -v "$PWD/runner":/src -w /src golang:1.26.3-bookworm go test ./...`. Keep `Version` in
  `runner/main.go` at the extension's version; `scripts.release` checks it.
- iceberg-go is pinned to a `main` commit in `runner/go.mod` (see `DECISIONS.md`). Move it on purpose:
  `go get github.com/apache/iceberg-go@<commit>`, `go mod tidy`, run the Go tests and the spike against a
  real Polaris and RustFS (`runner/spike_test.go`, build tag `spike`), then note it in `CHANGELOG.md`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`maintenance/bridge_client.py`) and standard Iceberg REST/S3.
- Every capability is a service method in `maintenance/services.py`, exposed as a REST operation
  (`maintenance/api.py`, listed in `OPERATIONS`) and an MCP tool (`maintenance/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/maintenance-api/v1/openapi.yaml` is
  stale: regenerate it with `uv run python -m scripts.api_contract`.
- Policy settings are an allowlist with bounds (`maintenance/policy.py`); the runner checks the job again
  (`runner/job.go`). Orphan cleanup never removes files younger than a day, whatever a job says.
- Authorization lives in `Services`. Health checks and dry runs get `read` tokens of the team's automation
  principal, maintenance `write` tokens.
- The launcher is the only component with the Docker socket: keep its inputs fixed (image, flags, mounts,
  one internal network). Runs need no internet.
- On Kubernetes (`LAUNCHER_BACKEND=kubernetes`, `launcher/kubernetes.py`) the launcher creates one Job
  per run in the runs namespace, with the catalog token (`MNT_TOKEN`) and a one-run ticket (`RUN_TICKET`)
  in a Secret the Job owns; it is the only container with a service account token. With `RUN_URL` set,
  the runner (`runner/remote.go`, standard library only) fetches its job from `GET /jobs/{id}/input` and
  returns `out/` with `PUT /jobs/{id}/output`, both with the ticket as Bearer. The chart's NetworkPolicy
  allows only DNS, the catalog, storage and the launcher. `launcher/kube.py` is a copy of the platform's
  `server/kube.py`. Keep `deploy/helm/iceberg-maintenance/Chart.yaml` at the extension's version;
  `scripts.release` checks it.
- The page follows the platform's design (`maintenance/public/style.css` starts from the user portal's
  tokens). Its CSP refuses style attributes: set sizes through the CSSOM, as `h()` does for `style`.
  `uv run python -m tests.dev_server` serves it with the platform faked.
