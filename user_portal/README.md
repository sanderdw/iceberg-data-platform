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
- **Catalog:** browse namespaces, tables and views. **Details →** shows a table's schema, snapshots, branches and tags, partitioning, sort order and properties, or a view's SQL and versions. **Load preview** reads up to 100 rows from any snapshot with your own permissions. Everything here is read-only.
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
| 06 · Write an AI-ready flights product | Publishing a synthetic data product with Apache Ossie semantics and quality checks | Yes |
| 07 · Read an AI-ready flights product | Semantic joins, metrics and quality evidence | No |

Run the write notebook before its reader.

## Data shares

**Data shares** lists the shares of the active team's databases, plus the shares that other teams have shared with you. Only Administrators and Database + bucket administrators can manage shares.

Choose **New data share** under a database, pick the tables and views, and optionally set an expiry. Then choose the recipient:

- **Another team:** its current and future members get read-only access with their own accounts. The database appears in their **Catalog** and **Notebooks**, even if they own no databases.
- **Externally:** the client ID and secret are shown once. **Copy DuckDB snippet** copies a runnable script. Send both over a secure channel. The recipient saves the script as `read_share_duckdb.py` and runs `uv run read_share_duckdb.py`.

The recipient can read only the selected objects and can't list anything. A view shares only its definition, so you must add every table it reads, and the recipient can read those tables in full. **Edit** changes the selection or expiry, **New secret** replaces the secret, and **Revoke** ends access immediately. To change the recipients, revoke the share and create a new one. See [the resource model](../docs/CONTEXT.md#data-shares) for the details.

## Connect from your computer

Use your own Python, DuckDB CLI or DBeaver instead of a notebook. You sign in as yourself, so your team role decides what you can read and write.

In **Catalog**, select a database and open **Connect from your computer**:

1. Download `iceberg_connect.py`. The portal fills in its Keycloak and catalog addresses. It holds no secret.
2. Run `uv run iceberg_connect.py login`, then approve the code in your browser. The refresh token is stored in `~/.config/iceberg-platform` with owner-only permissions.
3. Copy the snippet for your tool. `db-…` is the database's catalog name.

| Tool | How |
| --- | --- |
| Python | `from iceberg_connect import catalog, duckdb_connection`. `catalog("db-…")` returns a PyIceberg catalog that renews its token itself. `duckdb_connection("db-…")` returns DuckDB with the database attached as `lakehouse`. |
| DuckDB CLI | `duckdb -init <(uv run iceberg_connect.py duckdb db-…)` |
| DBeaver | Create a DuckDB connection. Run `uv run iceberg_connect.py duckdb db-…` and add each printed line under **Connection settings › Initialization › Bootstrap queries**. |

DuckDB attaches read-only. Add `--write` to write with your own permissions. DuckDB keeps the token it started with, and that token lasts one hour. After that, run the command again (or replace the `CREATE SECRET` line in DBeaver). `uv run iceberg_connect.py logout` revokes the sign-in. Keycloak, Polaris and RustFS listen on `127.0.0.1`, so this works on the machine that runs the platform.

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

Tools: `list_databases`, `list_namespaces`, `list_tables`, `describe_table`, `describe_view` and `preview_rows`. Team administrators also get `create_database`, `rename_database` and `delete_database`, which requires `confirm_name`. Platform administrators can add the [administration endpoint](../docs/admin-guide.md#connect-an-mcp-client) as a second server.

## Configuration

These settings live in the shared `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_PORT` | `3002` | Host port |
| `PORTAL_LAN_IP` | - | Extra binding with `compose.users.lan.yaml` |
| `MAX_NOTEBOOKS` | `8` | Maximum simultaneous notebook containers |
| `NOTEBOOK_MEMORY` | `1g` | Memory limit per notebook |
| `USER_COOKIE_SECURE` | `false` | Set to `true` behind HTTPS |
| `MCP_CALLBACK_PORT` | `3010` | OAuth callback port of MCP clients |
| `POLARIS_PUBLIC_URL` | `http://localhost:8181` | Catalog address given to external share recipients |
| `S3_ENDPOINT` | `http://localhost:9000` | Storage address for share recipients; fixed per database at creation |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Address of a reverse proxy, so sign-in limits apply per visitor |

Behind a reverse proxy, enable WebSockets and preserve the Host header. See the [security model](../SECURITY.md) for the isolation and trust boundaries.
