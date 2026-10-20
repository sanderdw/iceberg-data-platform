---
name: dbt-platform
description: Build, test and schedule dbt v2 pipelines on the Iceberg Data Platform through the dbt extension's MCP tools, or from a terminal with iceberg_dbt.py when no MCP server is registered. Use when a user asks to model, transform, clean or aggregate data in their team's Iceberg databases, to add a dbt model or test, to fix a failing dbt run, or to schedule or promote a pipeline.
---

# dbt pipelines on the Iceberg Data Platform

The dbt extension's MCP server (`iceberg-dbt`, at `http://localhost:3004/mcp`) runs dbt v2 OSS with DuckDB on
the team's Apache Iceberg databases. Every tool acts with the signed-in user's team role; builds
run as the team's service account. People follow the result in the pipeline viewer at
`http://localhost:3004`; each project's `url` opens it there.

## Connect

You need two MCP servers: the user portal's (`iceberg-user`) to find the tables to build on, and
the dbt extension's (`iceberg-dbt`) to build. Both sign in through Keycloak as the user. For Claude Code:

```bash
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
claude mcp add --transport http --client-id ext-dbt-mcp --callback-port 3010 iceberg-dbt http://localhost:3004/mcp
```

On a Kubernetes installation the addresses are `https://dbt.<domain>/mcp` and
`https://users.<domain>/mcp` instead (the viewer at `https://dbt.<domain>`); ask the user for the
domain. Other agents use the same addresses and client ids (`AGENTS.md` in the installation folder
shows the recipes). If the dbt address does not answer, the extension is not installed: stop and
tell the user to run the installer again with `ICEBERG_EXTENSIONS=dbt`, or on Kubernetes to register
it and install its chart (`kubernetes-install` skill, or the documentation's "Add extensions").

## Workflow

1. `whoami`: find the team, your role there (reader, writer or admin), its databases per
   environment and `enabledEnvironments`: where dbt may compile and build. Nothing compiles or
   builds in an environment that is not enabled. Only a team admin can `enable_environment`; ask
   the user first, because it lets dbt act for the whole team there.
2. Find what to build on with the user portal's tools: `list_tables` and `describe_table` for
   columns, and `preview_rows` for a few rows. Ask the user what the result must answer before you
   write SQL. Data that is not in the platform yet (an API, a file, another database) cannot be
   read by a run: runs have no internet. Load it first, as-is, into a raw namespace with a `uv`
   script on `iceberg_connect.py` (see "Loading data from somewhere else" in `AGENTS.md`), keep
   that script in the project under `scripts/`, and read the raw tables with `sources:`.
3. `list_projects`, or `create_project` (writer). A new project is empty: only `dbt_project.yml`,
   a README and the namespace macro. Add the sources, models and tests the user asks for.
4. Change code on a branch: `create_branch`, then `write_files` with full file contents
   (one commit per call; `null` deletes). `main` is never written directly.
5. `compile` the changed models, then `run` with `command="build"`, `ref=<branch>` in
   development. On failure read `get_run_logs`, fix, commit again, and rebuild with
   `select="<model>+"`. Development is one shared sandbox per team: every branch builds the same
   tables, so your build replaces what a build of `main` wrote. Build `main` again when you are done
   with a branch that was not merged.
6. Check the result: `preview_model` for rows, `get_pipeline` with `ref=<branch>` for the DAG and
   statuses of your branch, `get_model` for columns and tests. Without `ref` it shows the latest run
   of any branch; the pipeline viewer shows `main` unless the person picks a branch.
7. Ask a team admin to `merge_to_main`, passing the branch head they reviewed with `diff` as
   `revision`. Then build `main` in development: the pipeline viewer shows `main` and draws only what
   a run of `main` produced (the merge's `hint` says so). Acceptance and production build only
   revisions that were the head of `main`; ask the user before building or scheduling there.
8. When a mart answers business questions, offer to build its semantic model with the
   `semantic-model` skill (`.agents/skills/semantic-model`). Then Conversational BI and
   `query_semantic_model` answer from the mart's agreed metrics. `replace_table` keeps the table's
   identity on every build, so the model stays valid. Keep the model in the project, so it is
   reviewed and merged with the pipeline: write the Ossie document to
   `semantic_models/<database>/<namespace>/<name>.yml` (database by display name with `-` as `_`).
   Every successful `build` or `run` publishes it to that environment; `get_run` lists it under
   `summary.semanticModels` with any errors. Check it with the user portal's
   `describe_semantic_model`, whose publish warnings the run does not repeat. Field and dataset
   descriptions you leave out come from the column docs and table comment dbt wrote. The file wins
   over edits made elsewhere; deleting it does not delete the published model.

## From a terminal

`iceberg_dbt.py` gives dbt's own commands in a terminal, while dbt runs on the platform as the
team's service account. The installer puts it in the installation folder next to
`iceberg_connect.py` when the dbt extension is installed; the viewer serves a copy at
`/iceberg_dbt.py`. Use it when the person wants a terminal, or when you have no `iceberg-dbt` MCP
server but can run shell commands:

1. Sign-in is the person's: ask them to run `uv run iceberg_dbt.py login` and approve it in their
   browser. Never handle their password or token.
2. `uv run iceberg_dbt.py projects`, then `uv run iceberg_dbt.py clone <project>` into a new folder,
   and work inside that folder. `status` shows what changed locally since `clone` or `pull`.
3. Edit models and YAML as files. Every command runs `main` as it is on the platform, so local
   edits only count once they are on a branch: `push --branch <name> -m "<message>"`, then
   `build --ref <name>` (with `--select <model>+` for a part). Never claim a build tested local
   edits that were not pushed; the script warns when that happens.
4. Read the streamed log and the exit code (0 is success). `show --select <model> --limit 10`
   previews rows; `compile --inline "<sql>"` checks a query.
5. Ask the person before `--target acceptance` or `--target production` (they build only a head of `main`),
   and ask a team administrator to merge the branch in the viewer.

The local dbt MCP server (`uvx dbt-mcp`, with `DBT_PROJECT_DIR` on the cloned folder and
`DBT_PATH` on the executable script) runs the same commands for an agent. Not available on the
platform: `show --inline`, `list`, `debug`, `deps` and `clean`.

## Project rules

- `schema` is the Iceberg namespace. `+catalog_name` picks the team database by its display name,
  with `-` written as `_`. The same code runs in every environment.
- Keep `+materialized: replace_table`: it replaces rows in one Iceberg transaction and keeps the
  table's identity, data shares and docs. Use `incremental` (`unique_key`, `merge`) for large or
  append-only data. Iceberg views are not supported. Use `full_refresh=true` only when a table's
  columns change.
- Read other tables of the team with `sources:`; `database:` is the database's display name with
  `-` as `_`, as in `+catalog_name`.
- Runs have no internet: `read_json('https://…')` and other remote reads fail. Load outside data
  first (workflow step 2). Small, hand-maintained reference data, such as a mapping, can be a seed.
  Received data shares are not readable by runs.
- Never add `profiles.yml`, `catalogs.yml` or `packages.yml`.
- Describe every model and its important columns in YAML, and add `not_null`/`unique`/
  `accepted_values`/`relationships` tests. Descriptions become the table comment and column docs
  in the platform catalog; test results become the table's quality status. dbt v2 refuses test
  parameters at the top level: put them under `arguments:`.

  ```yaml
  data_tests:
    - not_null
    - relationships:
        arguments:
          to: ref('stg_carriers')
          field: carrier_code
    - accepted_values:
        arguments:
          values: ['XA', 'XB', 'XC']
  ```
- A project without seeds gets dbt's `UnusedResourceConfigPath` warning for `seeds.<project>`:
  `dbt_project.yml` sets `+catalog_name` for seeds ahead of the first one. It is harmless; keep it.
- SQL is DuckDB SQL.
- A run ends 15 minutes before its catalog token expires: after 45 minutes unless the platform
  administrator raised `POLARIS_TOKEN_LIFETIME`. Split long pipelines with `select`, or use
  `incremental` models.
