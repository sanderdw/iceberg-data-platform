# AI agents

Both portals serve a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp`, so an AI agent works with your data with your own permissions. On the first tool call the agent opens Keycloak in your browser; sign in with your own account.

| Server | Address | Keycloak client | For |
| --- | --- | --- | --- |
| User portal | `http://localhost:3002/mcp` | `iceberg-mcp` | Databases, catalog, semantic models and data shares, as you. See [MCP tools](mcp.md) |
| Administration portal | `http://localhost:3000/mcp` | `iceberg-mcp` | Teams, users and databases, with the `platform-admin` role. See the [administration guide](admin-guide.md#connect-an-mcp-client) |
| Conversational BI | `http://localhost:3007/mcp` | `ext-conversationalbi-mcp` | Governed questions over semantic models. See [Conversational BI](conversational-bi.md) |
| dbt pipelines | `http://localhost:3004/mcp` | `ext-dbt-mcp` | Building, testing and scheduling pipelines. See [dbt pipelines](dbt.md) |
| Ingestion (dlt) | `http://localhost:3008/mcp` | `ext-ingestion-mcp` | Loading APIs, databases and files. See [Ingestion (dlt)](ingestion.md) |

The extension servers exist only when that [extension](extensions.md) is installed. On Kubernetes the addresses are `https://users.<domain>/mcp`, `https://admin.<domain>/mcp` and `https://<host>.<domain>/mcp` for each extension; see [addresses and sign-in](install-kubernetes.md#addresses-and-sign-in). Use them in the commands below instead of the `localhost` ones.

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

Keycloak accepts the callback on any `localhost` or `127.0.0.1` port. Claude Code pins port 3010, which must equal `MCP_CALLBACK_PORT` in `.env`. If you change that port, run `docker compose run --build --rm keycloak-bootstrap` and register the server again. On Kubernetes the port is the chart's `mcpCallbackPort`, applied by `helm upgrade`.

## What the agent can do

Every team member can browse the catalog, preview rows and answer questions from semantic models. Writers also publish and delete semantic models; team administrators also create, rename and delete databases and manage data shares. Destructive tools require the exact name. An external share's client secret is returned once, so the agent sees it; hand it only to the recipient.

[MCP tools](mcp.md) describes every tool, and [MCP design](mcp-design.md) why the tools work the way they do. The [agent skills](agent-skills.md) in the installation folder teach an agent common tasks, such as building a semantic model or setting up a class.
