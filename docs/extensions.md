# Extensions

The core platform stays small: catalog, storage, identity, portals and notebooks. Larger
capabilities, such as conversational BI, dbt pipelines and ingestion with dlt, live in **extensions**. Each one has its
own Compose project, dependencies, images, version, changelog and CI, and talks to the core only
through the **Extension Bridge**. The core and an extension are released independently and work together as
long as both support the same contract version.

```mermaid
flowchart LR
    subgraph Core[iceberg-platform]
        Bridge[Extension Bridge /bridge/v1]
        Keycloak
        Polaris[Apache Polaris]
        RustFS
        Users[User portal]
    end
    subgraph Extension[Extension, e.g. iceberg-conversationalbi]
        App[Gateway: UI, API and MCP]
        Worker[Query worker]
    end
    Person[User or AI agent] -->|Keycloak sign-in: ext-id clients| App
    App -->|User context, automation principals, tokens| Bridge
    Worker -->|Short-lived Iceberg REST token| Polaris
    Worker -->|Vended credentials| RustFS
    Users -.->|Link| App
```

## The contract

The [Extension Bridge contract](../contracts/bridge/README.md) ships with every core release. It
gives an extension the signed-in user's teams and databases, and automation principals: service
accounts that act for one team in one environment, enabled only by that team's Administrators,
with short-lived catalog tokens whose lifetime the platform sets
(`POLARIS_TOKEN_LIFETIME`, one hour by default; each token reports it in `expiresIn`). Team shares the team received reach their read role. Extensions sign
in through their own Keycloak clients, whose tokens Polaris refuses. Any producer may describe a
table with the [table-property conventions](../contracts/bridge/v1/table-properties.md), which the
catalog shows as **Produced by**. See the [resource model](CONTEXT.md#automation-principals) for how
automation principals are stored, and [COMPATIBILITY.md](../contracts/bridge/COMPATIBILITY.md) for
versioning.

## Register an extension

```bash
uv run python -m scripts.setup --extension conversationalbi --origin http://localhost:3007 --handshake extensions/conversationalbi/.env.bridge
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

Recreating the platform services creates the extension's Keycloak clients and adds a link in the
user portal. On the administration portal's Teams page, platform administrators see every
automation principal and can revoke it. `--remove-extension <id>` unregisters an extension; its
clients are disabled on the next start.

## Available extensions

| Extension | Folder | What it adds |
| --- | --- | --- |
| Conversational BI | `extensions/conversationalbi` | Ask questions about your own and shared semantic models in a chat. Answers come from governed queries compiled from the model, shown as generative UI: query cards, charts, tables and KPIs. Also an API and MCP server |
| dbt pipelines | `extensions/dbt` | AI agents build, test and schedule dbt v2 pipelines on your team's tables through an API and MCP server. Each run is an isolated container that writes as the team's service account. A viewer shows each pipeline per environment, and writers edit a model there on a branch. Tables it builds show where they come from under **Produced by** |
| Ingestion (dlt) | `extensions/ingestion` | Load REST APIs, PostgreSQL and MySQL or MariaDB tables, and CSV, JSONL or Parquet files into your team's databases with declarative [dlt](https://dlthub.com/docs) pipelines, on demand or on a schedule. Through a page, an API and an MCP server. Each run is an isolated container that writes as the team's service account. Tables it loads show **Produced by dlt** |

Each extension documents its own installation in its folder. The release installer can also add
Conversational BI and dbt pipelines; see [Conversational BI](install.md#add-conversational-bi) and
[dbt pipelines](install.md#add-dbt-pipelines). Ingestion (dlt) is installed from its folder; see
[Add Ingestion (dlt)](install.md#add-ingestion-dlt). The tables it loads are ordinary Iceberg tables,
so a dbt project declares them as sources.
