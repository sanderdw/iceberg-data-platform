# Administration guide

The administration portal at http://localhost:3000 (on Kubernetes `https://admin.<domain>`) manages teams, users, databases and data shares. Sign in through Keycloak with an account that has the `iceberg-admin/platform-admin` client role. A team role never grants portal administration, including the Administrator role. For the service list, see [services](reference/services.md); to start and stop the platform, see [installation](install.md#update-or-restart).

You can set up teams, databases and users in three ways: in the portal, through an AI agent over [MCP](#connect-an-mcp-client), or with the [API](#api). The portal's **Getting started** page has instructions and examples for each, using this installation's address. Lists refresh every 30 seconds while visible, and navigation and filters are kept in the URL.

## Teams, databases and users

### Create a team

On the **Teams** page, create the team. Renaming a team later keeps its stable ID.

### Create a database

Usually a team Administrator creates the team's databases in the user portal, as the team needs them; see [user portal](user-portal.md). A platform administrator can also create one here: under **Databases**, choose the team and the environment (Development, Acceptance or Production). Each database is an Iceberg catalog with its own RustFS bucket, and names are unique within a team and environment. Team members get access right away, with their role in that team.

### Add a user

Under **Users**, choose one of:

- **Create Keycloak account:** creates the account and links it to the platform. Copy the temporary password that is shown once. The user must change it at first sign-in. Email invitations are not configured.
- **Link existing account:** search for the exact Keycloak username. The account keeps its password. Accounts are never linked automatically by matching names.

Select one or more teams and a role per team. Every user needs at least one team. Give a new team at least one **Administrator**, who then creates its databases and manages its data shares. The roles are listed in [roles and permissions](reference/roles.md).

### Change a user's access

**Edit access** changes a user's teams and the role in each team. A change to one team leaves the user's other teams untouched.

### Reset a password

**Reset password** issues a new temporary password. It only works for accounts that this portal created; reset a linked account in the Keycloak console.

### Revoke a user

**Revoke** removes the platform identity and its grants but keeps the Keycloak account. Active sessions are refused at their next check, and their notebooks stop within 30 seconds. If setup or revocation stops partway, use **Retry setup** or **Revoke** on that row.

### Rename, move or delete a database

Under **Databases**:

- A rename or move keeps the catalog ID, bucket and connection settings. After a move, members of the new team get access with their role there.
- Deletion is immediate and irreversible. It removes all tables, views, files, the bucket and the database's data shares. If cleanup fails, use **Resume deletion**.

### Delete a team

Move or delete its databases first. Deletion is refused if a user would lose their last team.

### Use the Keycloak console

The portal's user management uses the `iceberg-provisioner` service account in the `iceberg` realm. For anything else, use the Keycloak console at http://localhost:8080/admin: sign in as `admin` and select the **iceberg** realm. On Kubernetes the console is not published; open it with `kubectl -n iceberg port-forward svc/keycloak 8080` and sign in with `KEYCLOAK_ADMIN_PASSWORD` from the Secret `iceberg-platform`. You grant portal administration there by assigning the `iceberg-admin` client role `platform-admin`. **Revoke** refuses accounts that have this role.

## Extension service accounts

When an [extension](extensions.md) such as Conversational BI is registered, the Teams page lists its service accounts (automation principals): one per team, environment and extension, enabled by a team Administrator in the extension. **Revoke** stops the extension from acting for that team environment at once. It gets no new tokens, and tokens already issued expire within `POLARIS_TOKEN_LIFETIME` (one hour by default).

## Data shares

Team administrators create data shares in the [user portal](data-shares.md). The **Data shares** page lists every share with its database, team, objects, expiry and creator, and you can revoke any of them there. A database that has shares can't be moved until they are revoked.

Recipients outside this machine need addresses they can reach: see [deploying beyond localhost](security.md#deploying-beyond-localhost). A Kubernetes installation has them already. For a temporary class or demo, the [quick-share skill](agent-skills.md) sets this up through Cloudflare quick tunnels.

## Catalog browser

The **Catalog** page browses all Polaris catalogs, namespaces, tables, views and semantic models, including catalogs created outside the portal. It shows names only, never data, view SQL, semantic model definitions or credentials.

## Semantic models

Polaris 1.8 stores [Apache Ossie](https://github.com/apache/ossie) semantic models beside the tables they describe. The platform enables the beta feature with `ENABLE_SEMANTIC_MODELS` in `compose.yaml`. Set `POLARIS_SEMANTIC_MODELS=false` in `.env` and restart Polaris to turn it off (on Kubernetes: `polaris.semanticModels=false`). Polaris then answers 406 and the user portal lists no models.

- **Access:** the Reader role of a new database gets `SEMANTIC_MODEL_LIST` and `SEMANTIC_MODEL_READ`. Writers and administrators already have `CATALOG_MANAGE_CONTENT`, which covers creating, replacing and dropping models.
- **Deleting a database** removes its semantic models first. Polaris refuses to drop a namespace that still holds one.
- **Where users find them:** **Catalog** in the user portal (the administration portal's **Catalog** lists their names only), the MCP tools `list_semantic_models`, `describe_semantic_model`, `query_semantic_model` and `list_dimension_values` (writers also `publish_semantic_model` and `delete_semantic_model`), the installation's `semantic-model` agent skill, and the example notebook pairs 06 and 07 (DuckDB) and 09 and 10 (Polars), which publish and read one.
- **Beta:** the Polaris API may change in a later release. Check the [release notes](https://polaris.apache.org/releases/1.8.0/) before upgrading Polaris.

## Infrastructure

The **Infrastructure** page shows service health, per-container resources, PostgreSQL statistics, bucket totals and RustFS disk space. The data comes from the internal `monitor` collector. The page refreshes every 15 seconds, and buckets are scanned every five minutes. Probe history is kept in memory only. Set `MONITOR_PROJECTS` to choose which Compose projects are measured; on Kubernetes the collector lists the pods of the platform and notebooks namespaces, with CPU and memory from metrics-server. When you run the portal outside Compose, set `MONITOR_URL` to the collector's address.

## pgAdmin

With Compose only (the Kubernetes chart has no pgAdmin): at http://localhost:5050, expand **Iceberg Platform → Polaris metadata** and enter `POSTGRES_PASSWORD` from `.env`. This is Polaris's metadata database. Make all changes through the portal so that grants and bucket policies stay in sync.

## Connect an MCP client

The portal serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp` for platform administrators. Register it in your coding agent. Replace `http://localhost:3000` with your `PORTAL_ORIGIN` if you changed it, or on Kubernetes with `https://admin.<domain>`.

| Agent | Register the server | Sign in |
|---|---|---|
| Codex (CLI and IDE extension) | `~/.codex/config.toml`, see below | `codex mcp login iceberg-admin` |
| GitHub Copilot in VS Code | `.vscode/mcp.json`: `{"servers": {"iceberg-admin": {"type": "http", "url": "http://localhost:3000/mcp", "oauth": {"clientId": "iceberg-mcp"}}}}` | Start the server from `mcp.json` |
| GitHub Copilot CLI | `~/.copilot/mcp-config.json`, under `mcpServers`: `"iceberg-admin": {"type": "http", "url": "http://localhost:3000/mcp", "tools": ["*"], "oauthClientId": "iceberg-mcp", "oauthPublicClient": true}` | On first use. If asked for a client ID, enter `iceberg-mcp` |
| Claude Code | `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin http://localhost:3000/mcp` | On the first tool call |

Codex configuration:

```toml
[mcp_servers.iceberg-admin]
url = "http://localhost:3000/mcp"
scopes = ["openid", "profile", "offline_access"]

[mcp_servers.iceberg-admin.oauth]
client_id = "iceberg-mcp"
```

Keep the `scopes` line. Without it, Codex asks Keycloak for every scope it advertises, and the sign-in fails with `invalid_scope` ([openai/codex#35253](https://github.com/openai/codex/issues/35253)).

If GitHub Copilot reports that the server is "blocked by policy", your organization has turned off third-party MCP servers for Copilot. A GitHub organization owner must allow them.

Any other MCP client needs:
- the URL (streamable HTTP)
- OAuth with the public client ID `iceberg-mcp`, with no client secret and no dynamic client registration
- the scopes `openid`, `profile` and `offline_access`

Keycloak accepts the sign-in callback on any `localhost` or `127.0.0.1` port. Claude Code pins port 3010, which must equal `MCP_CALLBACK_PORT` in `.env` (the chart's `mcpCallbackPort` on Kubernetes). Sign in with your platform administrator account.

Every call requires the `platform-admin` role. The tools are the administration API's operations:

- **Read:** `get_overview`, `list_teams`, `list_databases`, `get_database_connection`, `browse_catalog`, `list_users`, `find_accounts`, `list_shares`, `list_automation_principals`
- **Change:** `create_team`, `update_team`, `create_database`, `rename_database`, `move_database`, `create_user`, `link_user`, `update_user_access`, `retry_user_setup`
- **Destructive:** `delete_team`, `delete_database` (requires `confirm_name`), `delete_user`, `revoke_share`, `revoke_automation_principal`

`create_user` returns the one-time password in the agent's transcript. Treat that transcript as confidential, and reset the password if in doubt. Users connect to the [user portal's endpoint](ai-agents.md) in the same way.

## API

Interactive documentation is at `/docs` on the portal (http://localhost:3000/docs with Compose) after sign-in. **Try it out** uses your session and adds the write headers. From a script, send the `iceberg_admin` cookie from your browser:

```bash
curl -b 'iceberg_admin=<cookie>' -H 'Content-Type: application/json' -H 'X-Portal-Request: 1' \
  -d '{"name": "marketing"}' http://localhost:3000/api/teams
```

The table lists the main endpoints; `/docs` has all of them, including Keycloak account management.

| Method | Path | Action |
| --- | --- | --- |
| GET / POST / DELETE | `/api/session` | Read session / sign in / sign out |
| GET | `/api/health` | Public process health |
| GET | `/api/overview` | Teams, databases, users and shares |
| GET | `/api/infrastructure` | Cached monitoring snapshot |
| GET | `/api/admin/explorer/databases` | List all Polaris catalogs |
| GET | `/api/admin/explorer/contents?database=name&namespace=part` | List contents; repeat `namespace` for nested paths |
| GET / POST | `/api/teams` | List / create teams |
| PATCH / DELETE | `/api/teams/{id}` | Edit / delete a team |
| GET / POST | `/api/databases` | List / create databases |
| PATCH / DELETE | `/api/databases/{id}` | Move with `{"team":"team-id"}` / delete including data |
| PATCH | `/api/databases/{id}/name` | Rename with `{"name":"new-name"}` |
| GET | `/api/databases/{id}/connection` | Iceberg connection details |
| DELETE | `/api/shares/{id}` | Revoke a data share |
| GET | `/api/automation-principals` | List extension service accounts |
| DELETE | `/api/automation-principals/{id}` | Revoke an extension service account |
| GET / POST | `/api/users` | List / create users |
| PATCH / DELETE | `/api/users/{id}` | Replace memberships with `{"memberships":[{"team":"team-id","role":"writer"}]}` / revoke |

Mutations require `Content-Type: application/json` and `X-Portal-Request: 1`, including a DELETE without a body. Errors return `{"error": ...}` with status 422 (validation), 404 (not found) or 409 (conflict).

## Operations

- Run one portal replica; see [deployment boundaries](develop/keycloak.md#deployment-boundaries).
- Creations and moves roll back when Polaris or RustFS fails. Database deletion can always be resumed.
- Polaris 1.8 does not create the PostgreSQL schema when it bootstraps, so `compose.yaml` and the Helm chart name it (`currentSchema=public` in the JDBC URL). A different schema must exist before the first start.
- List calls return pages when a client sends a `pageToken` (`LIST_PAGINATION_ENABLED`). Without a token the answer is complete, as the Iceberg REST specification requires, so DuckDB and PyIceberg see every table.
- To stop the platform or reset all data, see [stop and preserve data](install.md#stop-and-preserve-data), or on Kubernetes [uninstall](install-kubernetes.md#uninstall).
