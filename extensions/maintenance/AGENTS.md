# Working on the Maintenance extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `extensions/maintenance`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- The runner (`runner/`) is a Go module of its own. Without Go installed, use a container:
  `docker run --rm -v "$PWD/runner":/src -w /src golang:1.27.4-bookworm go test ./...`. Keep `Version` in
  `runner/main.go` at the extension's version; `scripts.release` checks it.
- iceberg-go is pinned to a `main` commit in `runner/go.mod` (see `DECISIONS.md`). Move it on purpose:
  `go get github.com/apache/iceberg-go@<commit>`, `go mod tidy`, run the Go tests and the spike against a
  real Polaris and RustFS (`runner/spike_test.go`, build tag `spike`), then note it in `CHANGELOG.md`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`maintenance/bridge_client.py`), standard Iceberg REST/S3 and Polaris's
  Policy API (`maintenance/polaris.py`), always with the team's automation principal's tokens.
- Every capability is a service method in `maintenance/services.py`, exposed as a REST operation
  (`maintenance/api.py`, listed in `OPERATIONS`) and an MCP tool (`maintenance/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/maintenance-api/v1/openapi.yaml` is
  stale: regenerate it with `uv run python -m scripts.api_contract`. Starting a run is the one operation
  with two tools: `inspect_database` (inspect, dry-run) and `run_maintenance` (maintain, destructive).
- The API follows the platform's conventions: camelCase in REST, snake_case tool and parameter names, and
  a tool returns exactly what its REST operation returns. Every response is built field by field by a
  `public_*` function in `services.py`, with times through `timestamp()` (RFC 3339, UTC, milliseconds);
  no stored row and no Bridge answer is passed on. The response models in `api.py` describe those shapes
  for the contract: change both together. A list is an object with one plural key. Errors are
  `MaintenanceError(status, message, code)`, one status per code.
- Policies are Apache Polaris policies, never rows of ours (`DECISIONS.md`): one per system type
  (`system.snapshot-expiry`, `system.data-compaction`, `system.metadata-compaction`,
  `system.orphan-file-removal`), attached to the catalog. Maintenance's own live in the namespace
  `_maintenance`. Exclusions attach the disabled `excluded-<type>` policies to namespaces and tables; SQLite
  records them because Polaris cannot list attachments. Updates send Polaris's `current-policy-version`;
  there is no revision of ours. `maintenance/policy.py` maps the API's settings onto policy content (an
  allowlist with bounds, unknown content keys kept) and back; the runner checks the job again
  (`runner/job.go`). Orphan cleanup never removes files younger than a day, whatever a job says.
- A run resolves each table's policies in the gateway (`polaris.resolve`: list the tables, then
  `applicable-policies` per table) with the run's own token, and the job carries them per table. Every
  action names the policy behind it.
- A database has one schedule, in SQLite (`schedules`), set with `PUT`/`DELETE
  /databases/{database}/schedule`, not through the policy. It needs a policy and runs at most once an hour,
  stricter than the other extensions' five minutes (see `DECISIONS.md`).
- The runner retries a commit Polaris refuses as a conflict (`table.ErrCommitFailed`) only when iceberg-go
  gave up on it: reload, plan again, commit, with bounded backoff (`runner/retry.go`). Do not add a retry
  around what iceberg-go already replays.
- Authorization lives in `Services`. Health checks, dry runs and reading policies get `read` tokens of the
  team's automation principal; maintenance and changing policies `write` tokens. So policies need
  Maintenance enabled in the environment.
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
  `uv run python -m tests.dev_server` serves it with the platform faked, Polaris included
  (`tests/conftest.py`, `FakePolaris`: extend it with every Polaris call the gateway makes).
