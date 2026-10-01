# Working with an Iceberg Data Platform installation

This folder holds an installed Iceberg Data Platform: Apache Iceberg tables in Apache Polaris, sign-in through Keycloak, a user portal and an administration portal. This file tells coding agents (and new people) how to read and write data as a user, how to use the platform's MCP servers and how to describe tables with a semantic model.

Everything you do runs **as a signed-in person**, with that person's team roles: Reader reads, Writer also creates and changes tables, views and semantic models, Admin also manages the team's databases and shares. There is no shared service account.

## Words used here

| Term | Meaning |
|---|---|
| team | Group of people with one role each; owns databases |
| database | One Iceberg catalog (Polaris warehouse) with its own bucket. Its id `db-…` is what every tool takes; display names repeat across environments |
| environment | development, acceptance or production |
| namespace | Folder of tables inside a database, written as a list of parts: `["sales"]`, `["sales", "eu"]` |
| semantic model | Apache Ossie document stored in a namespace: what the tables mean, their joins and agreed metrics |

## Ground rules for agents

- **Address databases by id** (`db-…`), never by display name. `list_databases` (MCP) or the portal's **Catalog** shows the ids.
- **Never print, log or store tokens or passwords.** Let `iceberg_connect.py` handle sign-in. Only the person can sign in: when a tool says you are not signed in, ask them to run `uv run iceberg_connect.py login` and approve the code in their browser.
- **Ask before destructive or shared changes:** deleting or renaming databases, replacing tables or semantic models, creating or changing data shares. Deleting a database destroys its tables, files and shares at once, also in production.
- **Read first, write second.** Look at the schema (`describe_table`) and a few rows (`preview_rows`) before writing.
- **Prefer development.** Try new loads and models in a development database before production.
- Use `uv` for Python: `uv run` runs a script with its inline dependencies.

## 1. Read and write data with `iceberg_connect.py`

`iceberg_connect.py` connects your own Python, DuckDB CLI or DBeaver to the platform as yourself. The copy in this folder points at a local installation; the user portal serves a copy with this installation's addresses filled in (**Catalog › Connect from your computer**).

```bash
uv run iceberg_connect.py login                  # once: approve the code in a browser (the person does this)
uv run iceberg_connect.py shell db-…             # DuckDB CLI with the database attached as `lakehouse`, read-only
uv run iceberg_connect.py shell db-… --write     # the same, writable with your own permissions
uv run iceberg_connect.py duckdb db-…            # SQL that attaches it, for DBeaver bootstrap queries
uv run iceberg_connect.py logout                 # revoke the sign-in
```

The sign-in is stored in `~/.config/iceberg-platform` with owner-only permissions and lasts until logout or expiry. DuckDB keeps the token it started with for one hour; connect again after that.

From Python, next to `iceberg_connect.py` (run with `uv run --with pyiceberg[pyarrow] --with duckdb your_script.py`, or give the script inline dependencies):

```python
from iceberg_connect import catalog, duckdb_connection

# DuckDB: SQL over the database, attached as `lakehouse`. Nested namespaces are one dotted schema name.
con = duckdb_connection("db-…")                       # write=True to write
con.sql('SELECT count(*) FROM lakehouse."sales"."orders"').show()

# PyIceberg: tables, schemas, snapshots and appends. It renews its token by itself.
cat = catalog("db-…")
cat.create_namespace_if_not_exists("sales")
table = cat.load_table(("sales", "orders"))
table.append(arrow_table)                             # a pyarrow.Table matching the table schema
```

### Loading data from somewhere else

To copy data in from another database or files, write a small `uv` script that reads the source in batches and appends each batch with PyIceberg (or `INSERT INTO` through a writable DuckDB connection):

- Create the namespace and table with an explicit schema first (`create_table_if_not_exists`), with lower-case column names.
- Clean while copying: trim `char(n)` padding, convert time zones to UTC (`timestamptz`), map sentinel values when the owner agrees.
- Read in batches of about 100,000 to 250,000 rows with a server-side cursor; every append is one Iceberg snapshot.
- Appends are not idempotent: running the same copy twice duplicates rows. Drop and recreate the table for a full reload, or filter on a timestamp for incremental loads.
- Keep source credentials in environment variables, never in the script or the table.
- Verify afterwards: compare row counts and spot-check rows with `describe_table` and `preview_rows`.

## 2. Use the MCP servers

Both portals serve a Model Context Protocol endpoint at `/mcp`. On the first tool call the agent opens Keycloak in the browser and the person signs in with their own account.

| Server | Address (local default) | For | Main tools |
|---|---|---|---|
| `iceberg-user` | `http://localhost:3002/mcp` | everyone | `list_databases`, `list_namespaces`, `list_tables`, `describe_table`, `describe_view`, `preview_rows` (up to 100 rows), `list_semantic_models`, `describe_semantic_model`, `query_semantic_model`, `list_shares`, `list_received_shares`; writers also `publish_semantic_model`, `delete_semantic_model`; team admins also `create_database`, `rename_database`, `delete_database`, `create_share`, `update_share`, `rotate_share_credential`, `delete_share` |
| `iceberg-admin` | `http://localhost:3000/mcp` | platform administrators | teams, users, databases and shares across the platform (`get_overview`, `create_team`, `create_user`, `create_database`, …) |

Register a server in your agent (replace `iceberg-user` and the address for the admin server):

- **Claude Code:** `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp`
- **Codex:** in `~/.codex/config.toml` add `[mcp_servers.iceberg-user]` with `url` and `scopes = ["openid", "profile", "offline_access"]`, and `[mcp_servers.iceberg-user.oauth]` with `client_id = "iceberg-mcp"`; then `codex mcp login iceberg-user`
- **GitHub Copilot in VS Code:** `.vscode/mcp.json`: `{"servers": {"iceberg-user": {"type": "http", "url": "http://localhost:3002/mcp", "oauth": {"clientId": "iceberg-mcp"}}}}`
- **GitHub Copilot CLI and other agents:** see the table in step 1 of `.agents/skills/semantic-model/SKILL.md`.

Notes:
- The MCP tools work with the person's grants and never run free-form SQL. `query_semantic_model` answers business questions from a model's agreed metrics; use `iceberg_connect.py` for anything else and for writing data.
- `publish_semantic_model` stores a model (Writer role). To replace one, pass the `entityVersion` that `describe_semantic_model` returned. Build and check models with the semantic-model skill below first.
- Claude Code's callback port must equal `MCP_CALLBACK_PORT` in `.env` (3010 by default).
- An MCP server that "failed to connect" with `ENOTFOUND` usually points at an address that no longer exists, such as an ended quick-share tunnel. Register it again with the current address.

## 3. Semantic models

A semantic model makes tables answerable: it states the grain, the joins, the agreed metrics with their SQL and the rules an AI must follow. For a business question, read the models of the namespace first (`list_semantic_models`, `describe_semantic_model`), then **answer with `query_semantic_model`**: name the metrics, the dimensions to split by (a time dimension can take a `day`, `week`, `month`, `quarter` or `year` grain; `via` picks the relationship when a dataset can be reached in several ways) and filters, for example:

```json
{"database": "db-…", "namespace": ["sales"], "model": "orders", "metrics": ["revenue"],
 "dimensions": [{"field": "PURCHASE.order_date", "grain": "day"}],
 "filters": [{"field": "PURCHASE.status", "op": "!=", "value": "cancelled"},
             {"field": "PURCHASE.order_date", "op": ">=", "value": "2026-09-24"}]}
```

It compiles the model's own SQL and joins, reads with the person's permissions and returns the rows with the metric definitions and the SQL it ran. Write your own SQL only when no model covers the question, and never redefine a metric the model has. All dates are UTC.

To create or improve one, use the skill in `.agents/skills/semantic-model`. It interviews the person about the questions the model must answer, profiles the data, drafts the model, checks every question and metric against the real tables, and publishes to Polaris only after approval. Its helper works on its own too:

```bash
SM="uv run .agents/skills/semantic-model/scripts/semantic_model.py"
$SM profile db-… sales orders customers                  # column statistics to prepare the interview
$SM draft   db-… sales orders customers --name orders > semantic-models/sales/orders.json
$SM check   db-… sales semantic-models/sales/orders.json --questions semantic-models/sales/orders.questions.sql
$SM publish db-… sales semantic-models/sales/orders.json --name orders --questions semantic-models/sales/orders.questions.sql
$SM show    db-… sales orders                             # the stored model, to edit and publish again
```

The portal shows models under **Catalog** (with a **Diagram** of the joins), data shares can include them, and Conversational BI answers from them.

## 4. Skills in this folder

| Skill in `.agents/skills` | Use it to |
|---|---|
| `semantic-model` | create or improve a semantic model, with an interview and quality checks |
| `demo-company` | fill the platform with a fictional company and accounts for a class (needs the admin MCP server) |
| `quick-share` | make the portals reachable from anywhere for a temporary session, and undo that |

Claude Code reads skills from `.claude/skills`; the pointers there lead to `.agents/skills`. See "Agent skills" in `docs/install.md`.

## 5. When something fails

| Message | Cause and fix |
|---|---|
| "You are not signed in" / "sign-in has expired" | The person runs `uv run iceberg_connect.py login` |
| "Could not reach …" | The platform is stopped, or `iceberg_connect.py` points at an old address: download it again from the portal |
| 403 / "Your role may not do this" | The person lacks the Writer (or Admin) role in the database's team |
| 406 on semantic models | Semantic models are switched off; an administrator sets `POLARIS_SEMANTIC_MODELS=true` in `.env` |
| DuckDB write fails with "Metadata-log exists but none of the entries were valid" | Clock skew; `iceberg_connect.py` already turns the lookup off, so connect through it and keep the host clock in sync |
