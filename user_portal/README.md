# Iceberg Workspaces

The user portal at http://localhost:3002. Team members sign in with Keycloak, pick a team and environment, and work with their databases in shared [marimo](https://marimo.io/) notebooks.

## Start

Start the [platform](../README.md#run-from-source) first, then:

```bash
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

For changes to the portal only, run `docker compose -f compose.users.yaml up -d --build --wait users`. This skips rebuilding the notebook image.

To open the portals from a phone or another computer for a class or demo, use the [`quick-share` agent skill](../.agents/skills/quick-share/SKILL.md). Sign-in needs HTTPS off `localhost`, so a plain port binding such as `compose.users.lan.yaml` is not enough on its own.

## Using the portal

The top menu has six sections, all scoped to the selected **Active team** and **Environment** (Development, Acceptance or Production). Your role is set per team, so you can write in one team and only read in another. The URL keeps your location, and lists refresh every 30 seconds.

- **Team overview:** the team's members and roles, its databases and your active notebooks.
- **Databases:** team Administrators create, rename and delete the team's databases. Renaming keeps the catalog ID and connection settings. Deletion is immediate and removes all data and shares, so you must type the name to confirm it. If cleanup fails, use **Resume deletion**. Moving a database to another team is a platform-administrator action.
- **Catalog:** browse namespaces, tables and views. **Details →** shows a table's schema, snapshots, branches and tags, partitioning, sort order and properties, or a view's SQL and versions. **Semantic models** are listed with them, with a diagram of their datasets and relationships; see [Semantic models](#semantic-models). **Load preview** reads up to 100 rows from any snapshot with your own permissions. Everything here is read-only.
  A table built by a pipeline, such as a dbt project, shows its description and a **Produced by** card: the last run, the source revision, the outcome of its tests, links to its upstream tables and a link back to the pipeline. Registered extensions also appear in the top navigation.
- **Notebooks:** open a database in marimo. Choose **Shared files** in the notebook selector to browse team notebooks and the examples.
- **Data shares:** see [below](#data-shares).
- **Getting started:** three ways to work with your data: the portal, [your own tools](#connect-from-your-computer) and an [AI agent](#connect-an-mcp-client). It includes the commands for this installation, filled in for a database of the active team.

The API is documented at http://localhost:3002/docs, and **Try it out** works with your session and permissions.

## Notebooks

Each team and environment has one shared `/work` filespace that holds all its notebooks, across databases. Teammates see each other's saved files. It is not a collaborative editor, though, so coordinate when you edit the same file.

Each session and database runs in its own container with your own credentials. Switching team or environment, signing out or letting the session expire stops your execution but keeps saved files, so save before you switch. The variables `catalog`, `table` and `df` are ready to use.

Notebooks have internet access. Packages you install with marimo's package installer (uv) go to `/tmp/packages` and disappear when the container stops. For permanent dependencies, add them to the `notebook` uv dependency group and rebuild the image.

A native DuckDB attachment caches its credentials. Reconnect it, or call `refresh_table_credentials`, to renew them or to query another table.

### Examples

New filespaces receive these notebooks. Existing ones receive only the files they are missing, and nothing is overwritten. Each notebook runs from top to bottom.

| Notebook | Shows | Needs write access |
| --- | --- | --- |
| 01 · Neighborhood data with PyIceberg | Creating a synthetic energy table with PyIceberg | Yes |
| 02 · Visualize with DuckDB | Querying and charting that table | No |
| 03 · Native DuckDB on Iceberg | Attaching Polaris directly in DuckDB: snapshots, SQL | No |
| 04 · Write Iceberg v3 with DuckDB | Iceberg v3: variant, nanosecond timestamps, geometry, defaults, deletion vectors | Yes |
| 05 · Read Iceberg v3 with DuckDB | Reading those v3 features, row lineage and time travel | No |
| 06 · Write an AI-ready flights product | Publishing synthetic flight tables after quality checks, and their Apache Ossie semantic model in Polaris | Yes |
| 07 · Read an AI-ready flights product | Reading the semantic model from Polaris, checking the data against it, and answering questions with its joins and metrics; no local files | No |

Run the write notebook before its reader.

## Semantic models

A semantic model says what the tables in a namespace mean: datasets that map to tables, their fields, the relationships between datasets and agreed metrics with their SQL. It follows the [Apache Ossie](https://github.com/apache/ossie) specification. Polaris 1.8 stores it in the namespace, next to the tables, and it never runs a metric.

- **See them:** open a namespace in **Catalog**. Semantic models are listed after tables and views. **Details →** shows the overview, the datasets with their fields (**Open table →** jumps to the Iceberg table), the metrics and relationships, and the stored definition. Reading needs the Reader role.
- **Create them:** run example notebook **06 · Write an AI-ready flights product**. It needs the Writer role; it publishes the flights tables and then their semantic model. Notebook **07** reads the model back with any role. Writers create, replace and remove models; an update is refused if someone else changed the model since you read it. A coding agent can build one with you through the installation's [`semantic-model` skill](../.agents/skills/semantic-model/SKILL.md), which interviews you about the questions the model must answer, and store it with the MCP tool `publish_semantic_model`.
- **See the structure:** the **Diagram** tab draws each dataset as a table with its fields and each relationship as a line between the join columns. Hover a dataset to follow its relationships, or select it to open its table.
- **Ask an AI agent:** the MCP tools `list_semantic_models` and `describe_semantic_model` return the same information, and `describe_semantic_model` with `include_definition` also returns the stored document. `query_semantic_model` answers questions from the agreed metrics: the agent names metrics, dimensions (with an optional time grain) and filters, and the portal compiles the model's SQL, joining only along its relationships, and runs it read-only with your own permissions in a sandboxed DuckDB process. It accepts only scalar and aggregate metric SQL over the model's columns, as Conversational BI does; publishing warns about metrics it would refuse.
- **Share them:** a data share can include semantic models. Recipients see exactly the shared models in **Catalog** and read them with their own account or the share credential; they can't list other models or change any.

Semantic models are a beta feature of Polaris. The platform switches them on; an administrator can turn them off ([semantic models](../docs/admin-guide.md#semantic-models)). The portal then simply lists none, and the notebook explains that they are switched off.

## Data shares

**Data shares** lists the shares of the active team's databases, plus the shares that other teams have shared with you. Only Administrators can manage shares.

Choose **New data share** under a database, pick the tables, views and semantic models, and optionally set an expiry. Then choose the recipient:

- **Another team:** its current and future members get read-only access with their own accounts. The database appears in their **Catalog** and **Notebooks**, even if they own no databases.
- **Externally:** the client ID and secret are shown once. **Copy DuckDB snippet** copies a runnable script. Send both over a secure channel. The recipient saves the script as `read_share_duckdb.py` and runs `uv run read_share_duckdb.py`.

The recipient can read only the selected objects and can't list anything. A view shares only its definition, so you must add every table it reads, and the recipient can read those tables in full. Selecting a semantic model selects the tables its datasets read; if you remove one, the form warns that the recipient can't query the model without it, and saving names every table that is still missing. External recipients load a shared model with a GET on the address shown with the credential. **Edit** changes the selection or expiry, **New secret** replaces the secret, and **Revoke** ends access immediately. To change the recipients, revoke the share and create a new one. See [the resource model](../docs/CONTEXT.md#data-shares) for the details.

## Connect from your computer

Use your own Python, DuckDB CLI or DBeaver instead of a notebook. You sign in as yourself, so your team role decides what you can read and write.

In **Catalog**, select a database and open **Connect from your computer**:

1. Download `iceberg_connect.py`. The portal fills in its Keycloak and catalog addresses. It holds no secret.
2. Run `uv run iceberg_connect.py login`, then approve the code in your browser. The refresh token is stored in `~/.config/iceberg-platform` with owner-only permissions.
3. Copy the snippet for your tool. `db-…` is the database's catalog name.

| Tool | How |
| --- | --- |
| Python | `from iceberg_connect import catalog, duckdb_connection`. `catalog("db-…")` returns a PyIceberg catalog that renews its token itself. `duckdb_connection("db-…")` returns DuckDB with the database attached as `lakehouse`. |
| DuckDB CLI | `uv run iceberg_connect.py shell db-…` opens the DuckDB CLI with the database attached, on macOS, Linux and Windows. It needs `duckdb` on your `PATH`. |
| DBeaver | Create a DuckDB connection. Run `uv run iceberg_connect.py duckdb db-…` and add each printed line under **Connection settings › Initialization › Bootstrap queries**. |

Sessions run in UTC, so dates cast from timestamps are the same on every computer; `SET TimeZone = '…'` changes that for one session. DuckDB attaches read-only. Add `--write` to write with your own permissions. DuckDB keeps the token it started with, and that token lasts one hour. After that, run the command again (or replace the `CREATE SECRET` line in DBeaver). `uv run iceberg_connect.py logout` revokes the sign-in.

The helper turns off DuckDB's metadata-log lookup (`SET iceberg_use_metadata_log = false`). With the lookup on, a catalog clock that runs ahead of your computer makes the next write to a table fail with "Metadata-log exists but none of the entries were valid". Keep the platform host's clock in sync with NTP anyway.

Keycloak, Polaris and RustFS listen on `127.0.0.1`, so out of the box this works only on the machine that runs the platform. The [quick-share skill](../docs/install.md#agent-skills) makes it work from anywhere: it publishes the catalog and S3 API on the user portal's address. The Getting started guide warns when the catalog address only works on the platform's own machine. The helper names the address it could not reach and tells the user to download it again.

## Connect an MCP client

The portal serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp`, so an AI agent can explore your data with your own permissions. Register it in your coding agent:

```bash
# Claude Code
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
```

Codex, in `~/.codex/config.toml`, then run `codex mcp login iceberg-user`:

```toml
[mcp_servers.iceberg-user]
url = "http://localhost:3002/mcp"
scopes = ["openid", "profile", "offline_access"]

[mcp_servers.iceberg-user.oauth]
client_id = "iceberg-mcp"
```

GitHub Copilot in VS Code, in `.vscode/mcp.json`:

```json
{"servers": {"iceberg-user": {"type": "http", "url": "http://localhost:3002/mcp", "oauth": {"clientId": "iceberg-mcp"}}}}
```

The [administration guide](../docs/admin-guide.md#connect-an-mcp-client) also covers GitHub Copilot CLI and other clients.

On first use, the agent opens Keycloak in your browser; sign in with your own account. Keycloak accepts the callback on any `localhost` or `127.0.0.1` port. Claude Code pins port 3010, which must equal `MCP_CALLBACK_PORT` in `.env`. If you change that port, run `docker compose run --build --rm keycloak-bootstrap` and register the server again.

Tools: `list_databases`, `list_namespaces`, `list_tables`, `describe_table`, `describe_view`, `list_semantic_models`, `describe_semantic_model`, `query_semantic_model` and `preview_rows`. Writers also get `publish_semantic_model`, which needs the `entityVersion` you read to replace a model, and `delete_semantic_model`, which requires `confirm_name`. The semantic-model tools are described under [semantic models](#semantic-models). Team administrators also get `create_database`, `rename_database` and `delete_database`, which requires `confirm_name`.

Data shares: `list_shares` and `list_received_shares` show shares with their objects, and `list_share_teams` names the teams that can receive one. Team administrators also get `create_share`, `update_share`, `rotate_share_credential` and `delete_share`; `delete_share` requires `confirm_name`. By default, sharing a semantic model also shares the tables it reads, as in the portal. `create_share` for an external share and `rotate_share_credential` return the client secret once, so the agent sees it; hand it only to the recipient. Platform administrators can add the [administration endpoint](../docs/admin-guide.md#connect-an-mcp-client) as a second server.

## Configuration

These settings live in the shared `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_PORT` | `3002` | Host port |
| `PORTAL_LAN_IP` | - | Extra binding with `compose.users.lan.yaml` |
| `MAX_NOTEBOOKS` | `8` | Maximum simultaneous notebook containers |
| `NOTEBOOK_MEMORY` | `2g` | Memory limit per notebook |
| `POLARIS_SEMANTIC_MODELS` | `true` | Set to `false` to turn off semantic models in Polaris |
| `USER_COOKIE_SECURE` | `false` | Set to `true` behind HTTPS |
| `MCP_CALLBACK_PORT` | `3010` | OAuth callback port of MCP clients |
| `POLARIS_PUBLIC_URL` | `http://localhost:8181` | Catalog address for `iceberg_connect.py` and external share recipients |
| `S3_ENDPOINT` | `http://localhost:9000` | Storage address that Polaris hands out with each table. On restart, the administration portal moves existing databases to it. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Address of a reverse proxy, so sign-in limits apply per visitor |

Behind a reverse proxy, enable WebSockets and preserve the Host header. See the [security model](../SECURITY.md) for the isolation and trust boundaries.
