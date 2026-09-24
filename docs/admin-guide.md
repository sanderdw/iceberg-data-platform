# Administration guide

The administration portal at http://localhost:3000 manages teams, users, databases and data shares. Sign in through Keycloak with an account that has the `iceberg-admin/platform-admin` client role. A team role never grants portal administration, including the Administrator role. For start-up commands and the service list, see the [README](../README.md#quick-start).

You can set up teams, databases and users in three ways: in the portal, through an AI agent over [MCP](#connect-an-mcp-client), or with the [API](#api). The portal's **Getting started** page has instructions and examples for each, using this installation's address.

## Teams, databases and users

1. **Create a team** on the Teams page. Renaming a team keeps its stable ID.
2. **Create a database** under Databases, or let a team Administrator create one in the user portal. Each database is an Iceberg catalog with its own RustFS bucket, in the Development, Acceptance or Production environment. Names are unique within a team and environment. Team members get access right away, with their role in that team.
3. **Create a user** under Users:
   - **Create Keycloak account:** creates the account and links it to the platform. Copy the temporary password that is shown once. The user must change it at first sign-in. Email invitations are not configured.
   - **Link existing account:** search for the exact Keycloak username. The account keeps its password. Accounts are never linked automatically by matching names.

   Select one or more teams and a role per team. Every user needs at least one team.
4. **Edit access** changes a user's teams and the role in each team. A change to one team leaves the user's other teams untouched.
5. **Reset password** issues a new temporary password. It only works for accounts that this portal created.
6. **Revoke** removes the platform identity and its grants but keeps the Keycloak account. Active sessions are refused at their next check, and their notebooks stop within 30 seconds. If setup or revocation stops partway, use **Retry setup** or **Revoke** on that row.
7. **Rename, move or delete a database** under Databases:
   - A rename or move keeps the catalog ID, bucket and connection settings. After a move, members of the new team get access with their role there.
   - Deletion is immediate and irreversible. It removes all tables, views, files, the bucket and the database's data shares. If cleanup fails, use **Resume deletion**.
8. **Delete a team** after moving or deleting its databases. This is refused if a user would lose their last team.

| Role | Portal label | Access to every database of that team |
| --- | --- | --- |
| `reader` | Read | Read namespaces, tables, views and data |
| `writer` | Read & write | Also create and write namespaces, tables and views |
| `admin` | Administrator | Also manage catalog access, databases and data shares |
| `bucket-admin` | Database + bucket administration | Administrator plus direct S3 credentials for the team's buckets |

The portal's user management uses the `iceberg-provisioner` service account in the `iceberg` realm. For anything else, use the Keycloak console at http://localhost:8080/admin: sign in as `admin` and select the **iceberg** realm. You grant portal administration there by assigning the `iceberg-admin` client role `platform-admin`. **Revoke** refuses accounts that have this role.

Lists refresh every 30 seconds while visible. Navigation and filters are kept in the URL.

## Data shares

Team administrators create data shares in the [user portal](../user_portal/README.md#data-shares). The **Data shares** page lists every share with its database, team, objects, expiry and creator, and you can revoke any of them there. A database that has shares can't be moved until they are revoked.

Recipients outside this machine need addresses they can reach. Put Polaris and RustFS behind your own TLS reverse proxy. Expose only `/api/catalog` of Polaris and only the S3 API of RustFS. Then set `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` in `.env` and restart. The administration portal then points the storage of existing databases at the new `S3_ENDPOINT`. For a temporary class or demo, the [quick-share skill](install.md#agent-skills) does all of this through Cloudflare quick tunnels. Read [the security model](../SECURITY.md) first.

## Catalog browser

The **Catalog** page browses all Polaris catalogs, namespaces, tables and views, including catalogs created outside the portal. It shows names only, never data, view SQL or credentials.

## Infrastructure

The **Infrastructure** page shows service health, per-container resources, PostgreSQL statistics, bucket totals and RustFS disk space. The data comes from the internal `monitor` collector. The page refreshes every 15 seconds, and buckets are scanned every five minutes. Probe history is kept in memory only. Set `MONITOR_PROJECTS` to choose which Compose projects are measured. When you run the portal outside Compose, set `MONITOR_URL` to the collector's address.

## pgAdmin

At http://localhost:5050, expand **Iceberg Platform → Polaris metadata** and enter `POSTGRES_PASSWORD` from `.env`. This is Polaris's metadata database. Make all changes through the portal so that grants and bucket policies stay in sync.

## Connect an MCP client

The portal serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp` for platform administrators. Register it in your coding agent. Replace `http://localhost:3000` with your `PORTAL_ORIGIN` if you changed it.

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

Keycloak accepts the sign-in callback on any `localhost` or `127.0.0.1` port. Claude Code pins port 3010, which must equal `MCP_CALLBACK_PORT` in `.env`. Sign in with your platform administrator account.

Every call requires the `platform-admin` role. The tools are the administration API's operations:

- **Read:** `get_overview`, `list_teams`, `list_databases`, `get_database_connection`, `browse_catalog`, `list_users`, `find_accounts`, `list_shares`
- **Change:** `create_team`, `update_team`, `create_database`, `rename_database`, `move_database`, `create_user`, `link_user`, `update_user_access`, `retry_user_setup`
- **Destructive:** `delete_team`, `delete_database` (requires `confirm_name`), `delete_user`, `revoke_share`

`create_user` returns the one-time password in the agent's transcript. Treat that transcript as confidential, and reset the password if in doubt. Users connect to the [user portal's endpoint](../user_portal/README.md#connect-an-mcp-client) in the same way.

## API

Interactive documentation is at http://localhost:3000/docs after sign-in. **Try it out** uses your session and adds the write headers. From a script, send the `portal_session` cookie from your browser:

```bash
curl -b 'portal_session=<cookie>' -H 'Content-Type: application/json' -H 'X-Portal-Request: 1' \
  -d '{"name": "marketing"}' http://localhost:3000/api/teams
```

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
| GET / POST | `/api/users` | List / create users |
| PATCH / DELETE | `/api/users/{id}` | Replace memberships with `{"memberships":[{"team":"team-id","role":"writer"}]}` / revoke |

Mutations require `Content-Type: application/json` and `X-Portal-Request: 1`, including a DELETE without a body. Errors return `{"error": ...}` with status 422 (validation), 404 (not found) or 409 (conflict).

## Operations

- Run one portal replica. Sessions are in memory and last at most eight hours, and a lock serializes changes.
- Creations and moves roll back when Polaris or RustFS fails. Database deletion can always be resumed.
- `docker compose down -v` deletes all platform data and resets the installation.
