# dbt pipelines

dbt v2 pipelines for your teams' tables. The extension is **API and MCP first**: AI agents create projects, commit models, compile, build, test and schedule, and people watch the pipelines in a viewer, where writers can also edit a model on a branch.

| | |
| --- | --- |
| Engine | dbt v2 OSS with its built-in DuckDB adapter and Iceberg REST catalogs |
| Viewer | `http://localhost:3004` (pipelines, runs, schedules, docs), linked from the user portal as **Pipelines** |
| API | `http://localhost:3004/api/v1` |
| MCP | `http://localhost:3004/mcp` (Keycloak sign-in through the `ext-dbt-mcp` client) |

## Install

The installer asks whether to add dbt. To install without questions:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | ICEBERG_EXTENSIONS=dbt sh
```

```powershell
$env:ICEBERG_EXTENSIONS = 'dbt'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

Each build runs in its own container, with no internet and a read-only file system, from the runner image the installer pulls. The extension's launcher starts these containers through the Docker socket; it accepts no image, flag or mount from a request. On [Kubernetes](install-kubernetes.md#add-extensions) each build is a Job in the namespace `<namespace>-dbt-runs` instead, with the same limits. Rerunning the installer keeps the extension, its projects and their runs.

A team administrator enables dbt once per environment, through an agent (`enable_environment`), under **Settings** in the viewer, or with `POST /api/v1/teams/{team}/environments/development`.

On Kubernetes, install its chart next to the platform's; see [Install on Kubernetes](install-kubernetes.md#add-extensions).

## Connect an AI agent

Register both the user portal MCP and the dbt MCP. The agent uses the user portal tools to find the team's databases and tables, and the dbt tools to build and manage pipelines.

```bash
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
claude mcp add --transport http --client-id ext-dbt-mcp --callback-port 3010 iceberg-dbt http://localhost:3004/mcp
```

For GitHub Copilot in VS Code, add both servers to `.vscode/mcp.json`:

```json
{
  "servers": {
    "iceberg-user": {
      "type": "http",
      "url": "http://localhost:3002/mcp",
      "oauth": { "clientId": "iceberg-mcp" }
    },
    "iceberg-dbt": {
      "type": "http",
      "url": "http://localhost:3004/mcp",
      "oauth": { "clientId": "ext-dbt-mcp" }
    }
  }
}
```

On first use, each server opens its Keycloak sign-in flow. Both act with the signed-in user's permissions. The `dbt-platform` [agent skill](agent-skills.md) in the installation folder teaches an agent the platform's conventions.

### MCP tools

What each role may do is in [roles and permissions](reference/roles.md#extensions).

| Tool | What it does |
| --- | --- |
| `whoami` | Your teams with your role in each, their databases per environment, where dbt is enabled, and this extension. |
| `list_environments` | Where dbt is enabled for a team, and the databases of each environment. |
| `enable_environment` / `disable_environment` | Lets dbt build in a team environment as the team's service account, or revokes it (team administrators). Disabling stops its runs and schedules. |
| `list_projects` | dbt projects of your teams, with your role in each. |
| `create_project` | Creates an empty project on `main`: `dbt_project.yml`, a README and the namespace macro (writers). |
| `get_project` | A project with its branches, environments, latest run per environment and schedules. |
| `delete_project` | Forgets a project: its Git history, runs, schedules and the dbt properties on the tables it built (team administrators). The tables, their data and docs stay. `confirm_name` must equal its name. |
| `list_files` / `read_file` | All files of a project, or one file's content, at a branch or revision. |
| `write_files` | Commits file changes to a branch in one commit (writers). `main` is never written directly. `expected_revision` refuses to overwrite a newer commit. |
| `list_branches` / `create_branch` / `delete_branch` | Branches with their latest commit; create one from `main` or another branch, or delete one (writers). `main` cannot be deleted. |
| `get_history` | Recent commits of a branch. |
| `diff` | Changed files and the patch between two branches or revisions. |
| `merge_to_main` | Merges a branch into `main` (team administrators). With `revision`, the merge is refused if the branch moved after you reviewed it. |
| `compile` | Compiles selected models, or inline SQL, with a read-only token, and returns the compiled SQL. |
| `preview_model` | Runs one model's query without writing and returns up to 100 rows. |
| `run` | Runs `build`, `run`, `seed`, `test`, `compile`, `parse` or `docs` as the team's service account. `build`, `run` and `seed` write tables (writers). Waits up to 600 seconds. |
| `list_runs` / `get_run` / `get_run_logs` | Recent runs; one run with its per-node results; the last lines of its dbt log. |
| `cancel_run` | Stops a running run (writers). |
| `list_schedules` / `set_schedule` / `delete_schedule` | A project's schedules; create, change or delete one, with cron in UTC. Development needs writers, other environments team administrators. |
| `get_pipeline` | The pipeline graph: nodes with type, table, latest status and test counts, and edges. |
| `get_model` | One node: description, columns with docs, SQL and compiled SQL, tests and latest result. |
| `get_lineage` | Upstream and downstream nodes of a node, up to `depth` steps. |

## Use dbt from your terminal

`iceberg_dbt.py` gives you dbt's commands in your own terminal while dbt runs on the platform, as the team's service account. Nothing runs on your computer and it needs no warehouse credentials. Download it from the viewer (**Projects › From your terminal**, or `/iceberg_dbt.py`): that copy holds this installation's addresses. It needs [uv](https://docs.astral.sh/uv/).

```bash
uv run iceberg_dbt.py login                    # sign in once in your browser
uv run iceberg_dbt.py projects
uv run iceberg_dbt.py clone cbs_energiebalans  # the project's files from main
cd cbs_energiebalans
uv run iceberg_dbt.py build --select balans+   # runs main in development; the log streams here
uv run iceberg_dbt.py test
uv run iceberg_dbt.py show --select balans --limit 5
uv run iceberg_dbt.py compile --inline "select count(*) from {{ ref('balans') }}"
```

Commands run `main` by default, in development unless you pass `--target acceptance` or `--target production` (see [promotion](#how-it-works)). Your local edits are not part of a run until you put them on a branch:

```bash
uv run iceberg_dbt.py status                   # what changed since clone or pull
uv run iceberg_dbt.py push --branch add-orders -m "Add orders"
uv run iceberg_dbt.py build --ref add-orders   # build the branch in development
```

A team administrator merges the branch into `main` in the viewer or with `merge_to_main`. Pass `revision` (the branch head you reviewed with `diff`) so the merge is refused if the branch moved since.

The [dbt MCP server](https://docs.getdbt.com/docs/dbt-ai/about-mcp?version=2#local-mcp-server) can run these commands for an AI agent. Make the script executable (`chmod +x iceberg_dbt.py`; it starts itself with `uv run`) and point `DBT_PATH` at it:

```json
{
  "mcpServers": {
    "dbt": {
      "command": "uvx",
      "args": ["dbt-mcp"],
      "env": {
        "DBT_PROJECT_DIR": "/path/to/cbs_energiebalans",
        "DBT_PATH": "/path/to/iceberg_dbt.py"
      }
    }
  }
}
```

Not available on the platform: `show --inline`, `list`, `debug`, `deps` and `clean`.

## How it works

- **Projects** belong to a team and are Git repositories kept by the extension. A new project is empty apart from `dbt_project.yml`, a README and the namespace macro. `main` is protected: writers commit to branches, and team administrators merge.
- **The viewer** shows each pipeline per environment and branch (`main` unless you pick another), follows running builds live, filters and focuses the graph (`+name+`), previews a model's rows and builds a node with its upstream or downstream. **Files** edits any project file on a branch, **Schedules** manages schedules and **Settings** enables environments and deletes branches. Writers create projects there. Team administrators delete them under **Settings**: the project, its history and the dbt properties on its tables go, the tables stay. Writers can edit a node's file from its panel, save it to a branch and build that branch in development. Team administrators can merge it to `main`.
- **Runs** execute as the team's service account for one environment, with a short-lived catalog token: `write` for `build`, `run` and `seed`, `read` for everything else. Two builds of the same project and environment never overlap.
- **Promotion.** Development builds any branch. Acceptance and production build only revisions that were the head of `main`: a merge always adds a merge commit, so a commit from inside a merged branch never reaches them. Their schedules need a team administrator.
- **Pipelines** are drawn from dbt's information schema, with each node's latest status and test results. After a build, each table gets the platform's producer, lineage and quality properties and its column docs, so the catalog shows where it comes from under **Produced by**.

What each team role may do in the viewer, API and MCP server is listed in [roles and permissions](reference/roles.md#extensions).

## Project conventions

- `schema` is the Iceberg namespace. `+catalog_name` picks the database by its display name, with `-` as `_`. A project runs unchanged in every environment.
- Use `+materialized: replace_table` (the platform materialization: rows are replaced in one Iceberg transaction, so the table keeps its identity, data shares and docs) or `incremental`. DuckDB cannot create Iceberg views. `table` recreates the table on every run.
- `profiles.yml` and `catalogs.yml` are generated for every run. `packages.yml` is not supported, because runs have no internet.
- Model and column descriptions become the table comment and column docs; tests become its quality status. Generic tests take their parameters under `arguments:`, as dbt v2 requires.
- Development is one shared sandbox per team: every branch builds the same tables, and the newest build wins. The viewer shows `main`, so build `main` after a merge to see it there.
- Semantic models are Apache Ossie documents at `semantic_models/<database>/<namespace>/<name>.yml`. Every successful build or run publishes them to its environment, next to the tables, and lists them in the run's summary. Descriptions a model leaves out come from the dbt models it reads. The file wins over edits made elsewhere; deleting the file does not delete the model.

## Limits

- A run ends 15 minutes before its catalog token expires, so after 45 minutes by default. For longer runs, raise the platform's token lifetime in `.env`, for example `POLARIS_TOKEN_LIFETIME=7200` for runs up to 1h45, and restart the platform with `docker compose up -d --wait`. On Kubernetes, set `polaris.tokenLifetime=7200` with `helm upgrade`. A revoked service account or data share then keeps its issued tokens up to that long.
- Runs cannot read data shares the team received.
- At most 4 runs run at once across all teams, and at most 2 of one team (on Kubernetes the chart's `runs.maxConcurrent` and `runs.maxPerTeam`). A waiting build holds no slot.
- The dbt docs site, linked from the viewer, loads DuckDB-WASM from cdn.jsdelivr.net in the browser.

How it is built: [dbt internals](develop/dbt.md).
