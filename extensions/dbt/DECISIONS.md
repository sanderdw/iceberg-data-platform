# Extension Bridge decisions for dbt (Phase 0 spike, 2026-09-29)

The spike ran dbt v2 against Apache Polaris 1.7.0 and RustFS 1.0.0 with a hand-made automation
principal. It recorded the choices below for Bridge contract 0.1. The only
component outside the contract is the dbt extension's runner image, which may change without a
contract change.

## Engine: dbt v2 OSS, installed from PyPI

- `dbt-oss==2.0.8` (Apache-2.0) on PyPI is a 5 KB source shim. Its build step installs the
  platform wheel with the Rust engine (`dbt/_core.abi3.so`) from dbt's GitHub release and checks the
  sha256 the shim pins. Wheels exist for Linux x86_64 and arm64 (manylinux_2_28), macOS and Windows.
  The dbt extension's `uv.lock` pins the shim's hash, so image builds are reproducible. The download
  happens at image build only.
- dbt v2 loads warehouse drivers through the ADBC driver manager. Without a local driver it
  downloads one from `public.cdn.getdbt.com`. Run containers have no internet, so the runner image
  registers the ADBC entry point `duckdb_adbc_init` of the pinned `duckdb==2.0.0` wheel in a driver
  manifest, `duckdb.toml`, found through `ADBC_DRIVER_PATH`. The runner runs the same DuckDB version
  as the platform's notebooks.
- The DuckDB extensions `iceberg`, `avro` and `httpfs` are installed at image build from DuckDB's
  core repository into `$HOME/.duckdb/extensions`, the way the platform's images get them. DuckDB
  refuses unsigned extensions, and `$HOME/VERSIONS` records the builds an image got. The
  `extension_directory` setting in the profile does not reach the attach that dbt generates.
- `DBT_SEND_ANONYMOUS_USAGE_STATS=false` stops all telemetry connections.
- dbt keeps state in `~/.dbt`, so the runner points `HOME` at `/tmp` and links the image's read-only
  DuckDB extensions there.
- The fallback engine, dbt-core 1.12 with dbt-duckdb, is not needed.

## Catalog attach

`catalogs.yml` (v2 schema; `use_catalogs_v2` is the default in dbt 2) is generated per run:

```yaml
catalogs:
  - name: sales                       # database display name in the team and environment
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: http://polaris-control-plane:8181/api/catalog
        warehouse: db-<hex>
        secret: polaris
        access_delegation_mode: vended_credentials
        support_nested_namespaces: true
        catalog_database: sales       # ASCII letters, digits and _ only
```

The profile declares the secret with `type: iceberg` and
`token: "{{ env_var('DBT_ENV_SECRET_POLARIS_TOKEN') }}"`. The token appeared in no artifact, log or
`target/` file.

`catalog_database` accepts only `[A-Za-z0-9_]`. Database names may contain `-`, so the dbt extension maps
`-` to `_` for the alias, and a model refers to a database by that alias.

## Tokens and least privilege: verified

- An automation principal with two principal roles, `svc-<hex>` (catalog role `writer`) and
  `svc-<hex>-read` (catalog role `reader`), gets tokens with `scope=PRINCIPAL_ROLE:<role>`.
- A read-scoped token can `compile`, `show` and read. Any write fails, because Polaris vends
  read-only storage credentials (`403` on the first data file).
- A write token can't write to a database of another team or environment.
- Polaris tokens last `POLARIS_TOKEN_LIFETIME` (3600 s by default; the platform sets it for its token
  broker and for vended storage credentials). A run ends 15 minutes before its token expires, so
  45 minutes by default; a platform with 7200 allows 1h45.

## Storage path: vended credentials (path A)

Polaris vends table-scoped STS credentials, and no S3 keys are ever handed out. Polaris puts
the host-facing `S3_ENDPOINT` (`http://localhost:9000`) in the vended configuration. The run
container therefore starts a loopback TCP forwarder `127.0.0.1:9000 → rustfs:9000`. The Host
header and the SigV4 signature are unchanged. When `S3_ENDPOINT` is not a loopback address (a quick
share or a reverse proxy, usually HTTPS), a run without internet cannot reach it, and forwarding
cannot help because TLS needs that host's certificate. dbt then reads the catalog through a
loopback proxy in the run (`runner/catalog_proxy.py`) that replaces the vended `s3.endpoint` by
the internal storage endpoint. DuckDB signs its requests for the internal host, which RustFS
accepts: vended credentials are not bound to a host name. The optional capability `storage-credentials` (path B) is reserved in the contract but
not implemented in 0.1.

## Materializations

| Materialization | Result |
| --- | --- |
| `view` | Not supported: `Not implemented: Create View` (DuckDB cannot create Iceberg views) |
| `table` | Works, but drops and recreates the table: the table UUID changes on every run, which breaks data shares and wipes table properties and column docs |
| `incremental` (`merge`, `unique_key`) | Works; keeps the table UUID |
| `seed`, data tests, `show`, `compile --inline` | Work |
| **`replace_table`** (platform) | `BEGIN; DELETE; INSERT; COMMIT` in one Iceberg transaction (one `transactions/commit`); keeps the table UUID, so shares and metadata survive |

New projects and the agent skill therefore default to `+materialized: replace_table`. The
dbt extension ships the materialization and a `generate_schema_name` macro that uses a model's `schema`
as the Iceberg namespace verbatim. dbt's default would prefix it with the target schema.

## Metadata for lineage and pipelines

`--generate-info-schema` writes Parquet to `target/info_schema/v1/`:

- `dbt.dag_nodes` (unique_id, resource_type) and `dbt.edges` (parent_unique_id, child_unique_id;
  macro edges included, filtered out by the extension)
- `dbt.models`, `dbt.seeds`, `dbt.sources`, `dbt.data_tests`, `dbt.node_columns` (descriptions)
- `dbt_rt.run_results` (status, timing, rows_affected)
- `dbt.column_lineage`, which stays empty in the OSS build: `--static-analysis strict` reports that
  "this distribution of dbt OSS does not include the static analysis engine"

The dbt extension reads these files with DuckDB. `run_results.json` (v6) and `manifest.json` are
also written. The information schema has no compiled SQL; it is in `target/compiled/<package>/<path>`. `dbt docs generate` builds a static, self-contained docs site; its DuckDB-WASM
location is set with `--duckdb-cdn-base`.

## Run jobs on Kubernetes (dbt 0.1.1, designed 2026-10-07, built 2026-10-17)

Implemented in dbt 0.1.1 (`launcher/kubernetes.py`, `runner/remote.py`, `deploy/helm/iceberg-dbt`), and
the same way in Ingestion 0.1.1. It differs from the design below in three places, found while building it:

- **Runs have a namespace of their own** (`<platform namespace>-dbt-runs`), not the launcher's: the right
  to create pods in a namespace exposes every Secret in it. That namespace holds `ExternalName`
  Services under the Bridge aliases, is held to the restricted Pod Security standard, and carries
  `io.iceberg-platform.isolated`, so Keycloak and the portals refuse its connections.
- **The launcher is a second container in the portal pod**, not a Deployment of its own, so both share
  the ReadWriteOnce artifacts volume. Only the launcher container gets a service account token.
- **The Docker backend still uses `put_archive` and `get_archive`.** The job contract (`RUN_URL`,
  `RUN_TICKET`, `GET /jobs/{id}/input`, `PUT /jobs/{id}/output`) is used by the Kubernetes backend
  only, so the Docker path did not change.

The design as proposed: this records how runs can move from the platform's Docker host to a Kubernetes
cluster, so that build capacity grows with nodes instead of with the platform machine.

### What ties a run to Docker today

Every run is already one container. The launcher (`launcher/app.py`), not the container, is
Docker-specific:

1. **Inputs are pushed in.** The launcher copies the staged `/work` (project at its revision,
   generated `profiles.yml` and `catalogs.yml`, `job.json`) into the container with `put_archive`.
2. **Outputs are pulled out.** After the run it copies `/work/out` back with `get_archive` to the
   artifacts volume that the portal reads.
3. **Isolation is a Docker network.** Each run gets an internal network, and the launcher connects
   the Polaris and RustFS containers to it under the aliases `polaris-control-plane` and `rustfs`,
   found by their `io.iceberg-platform.role` labels.
4. **Logs, exit code, timeout and cancel** go through the Docker API.

### Decision: one job contract, several launcher backends

The portal keeps talking to the launcher's HTTP API (`POST /runs`, `GET /runs/{id}`, logs, cancel),
unchanged. The launcher gets a backend switch, `LAUNCHER_BACKEND=docker|kubernetes`, with Docker
as the default. Both backends start the same pinned runner image under the same job contract:

- **The runner fetches and returns its own files.** It receives `RUN_URL` (the launcher) and a
  `RUN_TICKET`. It downloads the staged `/work` from `GET {RUN_URL}/jobs/{id}/input`, runs dbt, and
  uploads `out/` to `PUT {RUN_URL}/jobs/{id}/output`. The launcher stores that archive where the
  portal reads artifacts today, and checks it with the same rules as `collect`: regular files and
  directories under `out/` only, with a size limit.
- **The ticket** is random, bound to one run id, valid for one download and one upload, and expires
  with the run's timeout. It grants nothing else, and the launcher accepts it only on these two
  paths.
- **The catalog token** stays in `DBT_ENV_SECRET_POLARIS_TOKEN`, which dbt scrubs from its logs.
- **Endpoints come from `job.json`:** the catalog URI and the storage forwarding target, as
  `forward` already does. Docker aliases become Kubernetes Service names.
- **No backend accepts a choice from the portal**: image, security settings, mounts and network
  stay fixed in the launcher, as today.

The run now needs to reach the launcher, besides Polaris and storage. That is the only new path,
and it is limited to the two job endpoints.

### Mapping to Kubernetes

| Today (Docker) | Kubernetes backend |
| --- | --- |
| One container per run, pinned `RUNNER_IMAGE` | One `Job` per run (`backoffLimit: 0`), same image, `imagePullPolicy: IfNotPresent` |
| `read_only`, `cap_drop: ALL`, `no-new-privileges`, user `10001` | `securityContext`: `readOnlyRootFilesystem`, `capabilities.drop: [ALL]`, `allowPrivilegeEscalation: false`, `runAsNonRoot`, `runAsUser: 10001`, `seccompProfile: RuntimeDefault` |
| `mem_limit`, `nano_cpus`, `pids_limit` | `resources.requests` and `limits` for memory and CPU; the PID limit through the kubelet's `podPidsLimit` |
| `tmpfs /tmp`, anonymous volume `/work` | `emptyDir` volumes with `sizeLimit` (`medium: Memory` for `/tmp`) |
| Token as an environment variable | A `Secret` per run, owned by the Job, removed with it |
| Internal network with Polaris and RustFS | A `NetworkPolicy` for run pods: egress only to the launcher, Polaris, storage and DNS; no ingress |
| `container.wait(timeout)`, then kill | `activeDeadlineSeconds` set to the run timeout; `ttlSecondsAfterFinished` cleans up |
| Container logs | Pod logs through the API (`pods/log`) |
| Cancel: remove the container | Delete the Job (`propagationPolicy: Foreground`) |
| `recover`: remove labelled containers and networks | Delete Jobs with the run label at launcher start |

The launcher runs as a `Deployment` in the runs namespace, with a `Service` the run pods reach. Its
`ServiceAccount` gets a `Role` in that namespace only: create, get, list and delete `jobs`; get and
list `pods`; get `pods/log`; create and delete `secrets`. No cluster-wide rights. That is much
narrower than the Docker socket, which amounts to root on the host.

### What does not change

- The portal: API, MCP, viewer, Git repositories, roles, promotion rules and the Bridge tokens.
  It stays one instance with SQLite and the Git volume. Running several portal instances is a
  separate step (shared state and sessions).
- `MAX_CONCURRENT_RUNS` stays as the admission limit in the portal. The cluster schedules what is
  admitted, and node autoscaling adds capacity.
- The run limit, set by the platform's token lifetime (`POLARIS_TOKEN_LIFETIME`) minus 15 minutes.

### Open points

- **Storage address.** Polaris vends the browser-facing `S3_ENDPOINT`. The runner already handles
  both cases without reaching that address: the loopback forwarder, or the catalog proxy that
  points vended storage at the internal endpoint. On Kubernetes the internal endpoint is the
  storage Service.
- **Where the platform runs.** Runs in a cluster with the platform outside it need Polaris and
  storage reachable from the cluster, and the NetworkPolicy then names those addresses.
- **Image pulls.** Run pods pull the public `ghcr.io/sanderdw/iceberg-dbt-runner` image;
  air-gapped clusters need a mirror.

### Order of work

1. The job contract on the Docker backend: the runner fetches its input and uploads its output
   through the launcher, and `put_archive` and `get_archive` go. The behaviour stays the same,
   verified by `scripts/e2e_smoke.py` and the viewer flow.
2. The Kubernetes backend with example manifests (namespace, ServiceAccount and Role,
   NetworkPolicy, launcher Deployment and Service), tested on a local kind cluster in Docker.
3. Installation docs and a dbt release (shipped as dbt 0.1.1).

## dbt from your terminal, run on the platform (2026-10-07)

People want to type `build`, `test` or `show` in their own terminal, as the dbt docs teach
([local development](https://docs.getdbt.com/docs/local/about-local?version=2)), while the work runs
on the platform: as the team's service account, isolated, with the tables and grants of the
environment. dbt's own model for this is the dbt platform CLI: a local command whose runs happen
remotely. `iceberg_dbt.py` (served by the viewer at `/iceberg_dbt.py`) follows that model. It
never runs dbt locally and needs no warehouse credentials on the computer.

### Decision: commands run `main` by default

`build`, `run`, `test`, `show` and the other commands run the project's `main`, in development
unless `--target` names another environment. This keeps the platform simple:

- **One meaning for "the project".** What a terminal builds is what the viewer, schedules,
  acceptance and production build. No per-person branches appear on their own, and nothing is
  committed behind the user's back.
- **No new rules.** `main` stays protected: it changes only through a merge by a team
  administrator, exactly as through the API, MCP and viewer.

Git stays visible, as an explicit choice rather than a default:

- `clone`, `pull` and `status` show the revision of `main` the folder came from and what changed
  locally.
- Every command that runs `main` while local edits exist says so, and names the two steps that bring
  them along: `push --branch <name>` commits them to a branch, and `--ref <name>` runs that branch.
- `push` never writes `main`, and its output points at the merge a team administrator does.

The alternative, committing local edits to a personal branch before every run (as the dbt
platform CLI uploads its working tree), was set aside for now: it creates branches nobody asked
for and makes "what ran" depend on unpushed files. It can be added later as an option without
changing the default.

### Compatibility

- Sign-in is authorization code with PKCE on a loopback address through the existing public client
  `ext-dbt-mcp`, so no Keycloak change is needed. The refresh token is stored like
  `iceberg_connect.py`'s, with owner-only permissions.
- The command line takes dbt's shape (`--select`/`-s`, `--target`/`-t`, `--full-refresh`,
  `show --limit --output json`, `compile --inline`) and accepts and ignores flags the platform
  decides (`--quiet`, `--log-format`, colours, `--profiles-dir`). The local dbt MCP server
  (`uvx dbt-mcp`) uses the script as its `DBT_PATH`.
- Not available remotely: `show --inline`, `list`, `debug`, `deps` and `clean`. `target/` is not
  copied back; the viewer shows each run's results and lineage.

## Development is one shared sandbox per team (2026-10-11)

Every branch builds into the same development tables: a build of a branch replaces what a build of
`main` wrote, and the newest build wins. The pipeline viewer shows `main` and draws only `main`'s
runs, so after a branch build or a merge the tables and the picture can differ.

### Decision: keep it shared, and say so

Development stays one sandbox per team and environment. The platform makes the sharing visible
instead of hiding it:

- A build of a branch other than `main` in development returns a `hint` that it replaces the
  tables `main` builds.
- A branch's pipeline (`get_pipeline` with `ref`) carries `overwrittenBy` when another branch built
  development's tables after this branch's last build; the viewer turns it into a notice.
- `merge_to_main` returns a `hint` while `main` has no successful build of the merged revision in
  development, and the viewer offers **Build main in Development** after a merge.
- With nothing to draw on a branch, the pipeline names the branches that have a pipeline
  (`refsWithPipeline`) and the viewer offers to show them.

### Alternative set aside: a namespace per branch

Non-`main` branches could build into `<namespace>__<branch>`. That isolates people, but it changes
the `generate_schema_name` macro in every project, leaves tables behind when a branch is deleted
and its cleanup fails, and data shares and semantic models only ever point at `main`'s tables. It
can return as an option without changing the default.

## Semantic models live in the project (2026-10-11)

A semantic model describes a mart, so it changes with it. Published by hand, it lived outside Git:
not reviewed with the pipeline, not merged by a team administrator, and not rebuilt per environment.

### Decision: `semantic_models/` is published by the run

- A model is an Apache Ossie document at `semantic_models/<database>/<namespace…>/<name>.yml`. The
  first folder is the database's display name with `-` as `_`, as in `+catalog_name`; the folders
  after it are the namespace levels; the file name is the model's name. dbt does not read the
  folder.
- After a successful `build` or `run`, the runner (`runner/semantic.py`) stores each model in
  Polaris (`/api/catalog/polaris/v1/{warehouse}/namespaces/{ns}/semantic-models`) with the run's own
  write token, which already holds the Writer role. No Bridge change is needed. A failed run
  publishes nothing.
- The file wins: a model changed elsewhere is replaced on the next build. An unchanged model is
  left alone, so its version moves only when the file does. Removing a file does not delete the
  model; deleting stays an explicit, confirmed action in the user portal.
- The run reports `created`, `updated`, `unchanged` and `errors` under `summary.semanticModels`.
  Like table metadata, a publishing problem never fails the run.
- The platform's quality checks for models (descriptions, instructions, metric rules) stay in the
  user portal, which an extension may not import. The run checks only the file's place and shape;
  `describe_semantic_model` shows the result. Dataset and field descriptions a model leaves out
  come from the dbt descriptions of the models it reads, as the user portal's publish fills them
  from table comments and column docs.
