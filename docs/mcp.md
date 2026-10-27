# MCP tools

The user portal serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at
`/mcp`, so an AI agent works with your databases, semantic models and data shares as you. This page
describes every tool; [why the MCP tools work this way](mcp-design.md) explains the choices behind them. To register the
endpoint in a coding agent, see [AI agents](ai-agents.md).
Platform administrators have a separate endpoint on the administration portal, described in the
[administration guide](admin-guide.md#connect-an-mcp-client). [Extensions](extensions.md) serve their
own: Conversational BI at `http://localhost:3007/mcp` (client `ext-conversationalbi-mcp`), dbt at
`http://localhost:3004/mcp` (client `ext-dbt-mcp`) and Ingestion (dlt) at `http://localhost:3008/mcp`
(client `ext-ingestion-mcp`). On Kubernetes each is `/mcp` on its own HTTPS host; see
[addresses and sign-in](install-kubernetes.md#addresses-and-sign-in).

## How every tool works

- **As you.** The agent signs in through Keycloak with the public `iceberg-mcp` client. Each call
  carries your token. The portal re-reads your teams and roles from Polaris on every call, and
  Polaris applies your grants. A tool can never see or do more than you can in the portal.
- **By id.** Databases are addressed by their opaque id (`db-…`), never by name, because names repeat
  across environments. Namespaces are lists of parts, such as `["analytics", "nested"]`.
- **No free-form SQL.** Agents browse the catalog, preview rows and ask governed questions of a
  semantic model. They cannot send their own SQL.
- **Errors are answers.** A refused call returns a tool error with a reason the agent can act on, such
  as the metrics that do exist or the `entityVersion` it must read first.

## Tool reference

### Catalog

| Tool | What it does |
| --- | --- |
| `list_databases` | Your teams with your role in each, the databases they own, and `sharedDatabases`: databases of other teams shared with yours, read-only. Start here. |
| `list_namespaces` | Child namespaces of a database or of one namespace. |
| `list_tables` | Tables and views in one namespace. |
| `describe_table` | Schema with column comments, partitioning, sort order, branches and tags, and the latest snapshots. |
| `describe_view` | Schema and the SQL of the latest view versions. |
| `preview_rows` | Up to 100 rows rendered as text, at the current or a given snapshot. Uses a [query slot](mcp-design.md#query-slots). |

### Semantic models

| Tool | What it does |
| --- | --- |
| `list_semantic_models` | Names of the [Apache Ossie](https://github.com/apache/ossie) models stored in one namespace. |
| `describe_semantic_model` | Datasets, fields, relationships, metrics and AI instructions, and `queryable`: the dataset each metric counts, what it can be split by, and `problems`. It returns a summary by default. `search` and `dataset` narrow it, `detail="full"` returns every description and expression, and `include_definition` adds the stored document. |
| `query_semantic_model` | Answers a question with up to 5 metrics, 5 dimensions (`DATASET.field`, optionally with a `day`, `week`, `month`, `quarter` or `year` grain and the relationships to follow with `via`), 10 filters and 200 rows. It returns the rows, the metric definitions with their unit, the compiled SQL and parameters, and `dataAsOf`. Uses a query slot. |
| `list_dimension_values` | Distinct values of one dimension with their row counts. `search` keeps only values that contain it, ignoring case. Use it to find the exact value to filter on. Uses a query slot. |
| `publish_semantic_model` | Creates a model or replaces one (Writer role). Replacing it needs the `entityVersion` you read. It returns the warnings from validation. |
| `delete_semantic_model` | Deletes a model (Writer role). `confirm_name` must equal its name. |

### Databases

| Tool | What it does |
| --- | --- |
| `create_database` | Creates a catalog and its own bucket for a team you administer, in an environment. |
| `rename_database` | Changes a database's display name. Its id stays the same. |
| `delete_database` | Destroys the tables, views, files, bucket and shares of a database. `confirm_name` must equal its current name. |

### Data shares

| Tool | What it does |
| --- | --- |
| `list_share_teams` | Teams that can receive a share of a database. |
| `list_shares` / `list_received_shares` | Outgoing shares of a database, and the shares your teams received. |
| `create_share` / `update_share` | Share tables, views and semantic models with a team, an external party, the data catalog (`listed`, with a `contact`) or a combination. A listing publishes metadata only and needs a data catalog extension. By default, selecting a semantic model also selects the tables it reads (`addedTables`). |
| `rotate_share_credential` | Returns a new client secret for an external share, once. The old one stops working. |
| `delete_share` | Revokes all access immediately. `confirm_name` must equal the share's name. |

Only team administrators create, change and delete databases and shares. The tools say so when you
are not one.
