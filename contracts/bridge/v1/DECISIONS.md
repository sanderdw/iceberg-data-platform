# Bridge v1 decisions (Phase 0 spike, 2026-09-29)

The spike ran dbt v2 against Apache Polaris 1.7.0 and RustFS 1.0.0 with a hand-made automation
principal. It recorded the choices below before the contract was frozen at 1.0.0. The only
component outside the contract is the dbt stack's runner image, which may change without a
contract change.

## Engine: dbt v2 OSS, installed from PyPI

- `dbt-oss==2.0.5` (Apache-2.0) on PyPI is a 5 KB source shim. Its build step installs the
  platform wheel with the Rust engine (`dbt/_core.abi3.so`) from dbt's GitHub release and checks the
  sha256 the shim pins. Wheels exist for Linux x86_64 and arm64 (manylinux_2_28), macOS and Windows.
  The dbt stack's `uv.lock` pins the shim's hash, so image builds are reproducible. The download
  happens at image build only.
- dbt v2 loads warehouse drivers through the ADBC driver manager. Without a local driver it
  downloads one from `public.cdn.getdbt.com`. Run containers have no internet, so the runner image
  registers the ADBC entry point `duckdb_adbc_init` of the pinned `duckdb==1.5.5` wheel in a driver
  manifest, `duckdb.toml`, found through `ADBC_DRIVER_PATH`. The DuckDB version is therefore the same
  one the notebooks use.
- The DuckDB extensions `iceberg`, `avro` and `httpfs` come from the matching PyPI wheels
  (`duckdb-extension-*==1.5.5`). They are copied into `$HOME/.duckdb/extensions` at image build.
  The `extension_directory` setting in the profile does not reach the attach that dbt generates.
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

`catalog_database` accepts only `[A-Za-z0-9_]`. Database names may contain `-`, so the dbt stack maps
`-` to `_` for the alias, and a model refers to a database by that alias.

## Tokens and least privilege: verified

- An automation principal with two principal roles, `svc-<hex>` (catalog role `writer`) and
  `svc-<hex>-read` (catalog role `reader`), gets tokens with `scope=PRINCIPAL_ROLE:<role>`.
- A read-scoped token can `compile`, `show` and read. Any write fails, because Polaris vends
  read-only storage credentials (`403` on the first data file).
- A write token can't write to a database of another team or environment.
- Polaris tokens last 3600 s, so one run is capped at 45 minutes, leaving a margin.

## Storage path: vended credentials (path A)

Polaris vends table-scoped STS credentials, and no S3 keys are ever handed out. Polaris puts
the host-facing `S3_ENDPOINT` (`http://localhost:9000`) in the vended configuration. The run
container therefore starts a loopback TCP forwarder `127.0.0.1:9000 → rustfs:9000`. The Host
header and the SigV4 signature are unchanged. When `S3_ENDPOINT` is not a loopback address (a quick
share or a reverse proxy), the runner resolves that host name to RustFS through `extra_hosts`
instead. The optional capability `storage-credentials` (path B) is reserved in the contract but
not implemented in 1.0.

## Materializations

| Materialization | Result |
| --- | --- |
| `view` | Not supported: `Not implemented: Create View` (DuckDB cannot create Iceberg views) |
| `table` | Works, but drops and recreates the table: the table UUID changes on every run, which breaks data shares and wipes table properties and column docs |
| `incremental` (`merge`, `unique_key`) | Works; keeps the table UUID |
| `seed`, data tests, `show`, `compile --inline` | Work |
| **`replace_table`** (platform) | `BEGIN; DELETE; INSERT; COMMIT` in one Iceberg transaction (one `transactions/commit`); keeps the table UUID, so shares and metadata survive |

The starter project and the agent skill therefore default to `+materialized: replace_table`. The
dbt stack ships the materialization and a `generate_schema_name` macro that uses a model's `schema`
as the Iceberg namespace verbatim. dbt's default would prefix it with the target schema.

## Metadata for lineage and pipelines

`--generate-info-schema` writes Parquet to `target/info_schema/v1/`:

- `dbt.dag_nodes` (unique_id, resource_type) and `dbt.edges` (parent_unique_id, child_unique_id;
  macro edges included, filtered out by the stack)
- `dbt.models`, `dbt.seeds`, `dbt.sources`, `dbt.data_tests`, `dbt.node_columns` (descriptions)
- `dbt_rt.run_results` (status, timing, rows_affected)
- `dbt.column_lineage`, which stays empty in the OSS build: `--static-analysis strict` reports that
  "this distribution of dbt OSS does not include the static analysis engine"

The dbt stack reads these files with DuckDB. `run_results.json` (v6) and `manifest.json` are
also written. The information schema has no compiled SQL; it is in `target/compiled/<package>/<path>`. `dbt docs generate` builds a static, self-contained docs site; its DuckDB-WASM
location is set with `--duckdb-cdn-base`.
