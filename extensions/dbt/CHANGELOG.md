# Changelog

## 0.1.0 - 2026-10-13

- First version of the dbt extension for the Iceberg Data Platform, on Extension Bridge contract 0.1.
- dbt v2 OSS 2.0.5 with DuckDB 1.5.5 on Iceberg REST, installed without run-time downloads.
- Git-backed projects per team with protected `main`, branches, commits, diffs and merges. A new
  project starts empty, with only `dbt_project.yml`, a README and the namespace macro.
- Runs and schedules as the team's automation principal per environment, in isolated containers
  with short-lived read or write tokens. Acceptance and production build only `main`. A run ends
  15 minutes before its token expires, so runs grow with the platform's `POLARIS_TOKEN_LIFETIME`.
- A fair run queue: at most `MAX_CONCURRENT_RUNS` runs at once and `MAX_RUNS_PER_TEAM` per team,
  and a build waiting for its project holds no slot. Logs and files of older runs are removed after
  `RUN_RETENTION` runs per project and environment.
- Runs reach storage when the platform's `S3_ENDPOINT` is a public HTTPS address, such as a quick
  share: a catalog proxy in the run points vended storage at the internal endpoint.
- `replace_table` materialization that keeps Iceberg table identity, data shares and docs.
- Producer, lineage and quality table properties and column docs after every build.
- Semantic models as code: Apache Ossie documents at `semantic_models/<database>/<namespace>/<name>.yml`
  are published to the run's environment after every successful build or run, and reported in the
  run's summary. The file wins over edits made elsewhere; an unchanged model keeps its version.
- `whoami` lists where dbt is enabled per team (`enabledEnvironments`), and a new project says when
  development still has to be enabled before it can build.
- Development is one shared sandbox per team. A branch build says it replaces the tables `main`
  builds, a branch's pipeline says when another branch built those tables since, and a merge says
  when `main` still needs a build before the viewer shows it.
- REST API and MCP server with the same capabilities; pipeline graph and lineage from dbt's
  Parquet information schema.
- Pipeline viewer, full window width: pick a branch to see its pipeline, node statuses and runs
  (`main` by default; `ref` in the API and MCP tools). Filter the graph by status and node type,
  focus it with dbt's `+name+` operators, preview a model's rows and build a node with its upstream
  or downstream from its panel. Sources show no status dot: dbt reads them but never builds them
  (`external` in the API). With nothing drawn on a branch, the viewer offers the branches that have a
  pipeline. Statuses and the run list follow running builds live, and a writer
  stops a run in any phase (it ends `cancelled`). The timeline leaves out previews.
- Files view: every project file at a branch, YAML, seeds and tests included. Writers edit, add
  and delete files on a branch; `main` is read-only there.
- Schedules are created, paused and deleted in the viewer; Settings enables or disables dbt per
  environment (team administrators) and lists and deletes branches. Writers edit a node's file from its panel,
  save it to a branch and build that branch in development; team administrators merge it to `main`.
- `iceberg_dbt.py`, downloaded from the viewer: dbt's commands (`build`, `run`, `test`, `seed`,
  `show`, `compile`, `parse`, `docs`) in your own terminal, run on the platform with the log
  streamed back. Commands run `main`; `clone`, `pull`, `status`, `push --branch` and `--ref` bring
  local edits along through a branch. The local dbt MCP server can use it as `DBT_PATH`.
- Writers create a project in the viewer. Team administrators delete one there or through the API
  and MCP: deleting forgets the project as if it never existed, including the dbt producer, lineage
  and quality properties on the tables it built. Tables, their data and docs stay.
