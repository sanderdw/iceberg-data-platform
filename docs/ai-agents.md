# AI agents

Both portals serve a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp`, so an AI agent works with your data with your own permissions. On the first tool call the agent opens Keycloak in your browser; sign in with your own account.

| Server | Address | Keycloak client | For |
| --- | --- | --- | --- |
| User portal | `http://localhost:3002/mcp` | `iceberg-mcp` | Databases, catalog, semantic models and data shares, as you. See [MCP tools](mcp.md) |
| Administration portal | `http://localhost:3000/mcp` | `iceberg-mcp` | Teams, users and databases, with the `platform-admin` role. See the [administration guide](admin-guide.md#connect-an-mcp-client) |
| Conversational BI | `http://localhost:3007/mcp` | `ext-conversationalbi-mcp` | Governed questions over semantic models. See [Conversational BI](conversational-bi.md) |
| dbt pipelines | `http://localhost:3004/mcp` | `ext-dbt-mcp` | Building, testing and scheduling pipelines. See [dbt pipelines](dbt.md) |
| Ingestion (dlt) | `http://localhost:3008/mcp` | `ext-ingestion-mcp` | Loading APIs, databases and files. See [Ingestion (dlt)](ingestion.md) |

The extension servers exist only when that [extension](extensions.md) is installed.

## Connect the user portal

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

The [administration guide](admin-guide.md#connect-an-mcp-client) also covers GitHub Copilot CLI and other clients.

Keycloak accepts the callback on any `localhost` or `127.0.0.1` port. Claude Code pins port 3010, which must equal `MCP_CALLBACK_PORT` in `.env`. If you change that port, run `docker compose run --build --rm keycloak-bootstrap` and register the server again.

## What the agent can do

Tools: `list_databases`, `list_namespaces`, `list_tables`, `describe_table`, `describe_view`, `list_semantic_models`, `describe_semantic_model`, `query_semantic_model`, `list_dimension_values` and `preview_rows`. Writers also get `publish_semantic_model`, which needs the `entityVersion` you read to replace a model, and `delete_semantic_model`, which requires `confirm_name`. The semantic-model tools are described under [semantic models](semantic-models.md). Team administrators also get `create_database`, `rename_database` and `delete_database`, which requires `confirm_name`.

Data shares: `list_shares` and `list_received_shares` show shares with their objects, and `list_share_teams` names the teams that can receive one. Team administrators also get `create_share`, `update_share`, `rotate_share_credential` and `delete_share`; `delete_share` requires `confirm_name`. By default, sharing a semantic model also shares the tables it reads, as in the portal. `create_share` for an external share and `rotate_share_credential` return the client secret once, so the agent sees it; hand it only to the recipient.

[MCP tools](mcp.md) describes every tool and why it works the way it does. The [agent skills](agent-skills.md) in the installation folder teach an agent common tasks, such as building a semantic model or setting up a class.
