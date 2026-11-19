# Extensions

The core platform stays small: catalog, storage, identity, portals and notebooks. Larger capabilities live in **extensions**. Each one has its own version and releases, and works with your data only through the platform's sign-in, catalog and storage, with the same grants as you.

| Extension | What it adds |
| --- | --- |
| [Conversational BI](conversational-bi.md) | Ask questions about your own and shared semantic models in a chat. Answers come from governed queries compiled from the model, shown as generative UI: query cards, charts, tables and KPIs. Also an API and MCP server |
| [dbt pipelines](dbt.md) | AI agents build, test and schedule dbt v2 pipelines on your team's tables through an API and MCP server. Each run is an isolated container (a Kubernetes Job on [Kubernetes](install-kubernetes.md)) that writes as the team's service account. A viewer shows each pipeline per environment, and writers edit a model there on a branch. Tables it builds show where they come from under **Produced by** |
| [Ingestion (dlt)](ingestion.md) | Load REST APIs, PostgreSQL and MySQL or MariaDB tables, and CSV, JSONL or Parquet files into your team's databases with declarative [dlt](https://dlthub.com/docs) pipelines, on demand or on a schedule. Through a page, an API and an MCP server. Each run is an isolated container (a Kubernetes Job on Kubernetes) that writes as the team's service account. Tables it loads show **Produced by dlt** |
| [Replication (OLake)](replication.md) (beta) | Keep operational PostgreSQL databases in step with your team's databases: [OLake](https://github.com/datazip-inc/olake-go) reads inserts, updates and deletes from the write-ahead log (change data capture) every few minutes and writes them as positional deletes that every engine reads. Source checks, a page, an API and an MCP server. Each run is an isolated container (a Kubernetes Job on Kubernetes) that writes as the team's service account. Tables show **Produced by olake** |
| [Maintenance](maintenance.md) (beta) | Keeps your tables in shape: expires old snapshots, compacts small files and applies their deletes, removes dangling delete files, merges manifests and removes orphan files, by a policy per database, on a schedule or on demand. Table health page, API and MCP server. Each run is an isolated container (a Kubernetes Job on Kubernetes) that acts as the team's service account |
| [Evaluations](evaluations.md) | Question sets with known answers for Conversational BI: scored runs on demand or on a schedule, comparisons between runs and LLMs, and a live check of real conversations for teams that opt in. Results are Iceberg tables with a semantic model in the team's own database. Needs Conversational BI |
| [OpenMetadata](openmetadata.md) | Publishes the data shares that team administrators list to [OpenMetadata](https://open-metadata.org/): tables, views, columns and descriptions, semantic model metrics with their dimensions, glossary terms and field labels, lineage, and a data product per share with whom to ask for access. Metadata only, never data |

The tables Ingestion and Replication write are ordinary Iceberg tables, so a dbt project declares them as sources.

**Ingestion or Replication?** Use [Ingestion (dlt)](ingestion.md) for REST APIs, files and one-time or occasional loads of a database table. Use [Replication (OLake)](replication.md) to keep an operational database in step, with its updates and deletes, every few minutes.

## Install

The installer adds extensions. In a terminal it asks for each one. To install without questions, name them in `ICEBERG_EXTENSIONS`:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | \
  ICEBERG_EXTENSIONS=conversationalbi,dbt,ingestion,replication,maintenance,evaluations sh
```

```powershell
$env:ICEBERG_EXTENSIONS = 'conversationalbi,dbt,ingestion,replication,maintenance,evaluations'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

`ICEBERG_EXTENSIONS=none` skips the questions. Add `pgadmin` to the list for [pgAdmin](admin-guide.md#pgadmin), which shows the Polaris metadata database. Rerunning the installer keeps the extensions you added, with their data. Each extension page describes its own options. The installer doesn't add OpenMetadata: it needs your OpenMetadata server and a bot token, so it has [its own installer](openmetadata.md#install).

On Kubernetes, extensions have their own Helm charts; see [Install on Kubernetes](install-kubernetes.md#add-extensions). OpenMetadata has no chart yet and runs with Compose only.

## Service accounts

An extension acts for a team through a service account (an automation principal) for one environment. Only a team administrator enables it, in the extension. The account can write to the team's databases in that environment, and reads the data shares the team received there. Platform administrators see every service account on the administration portal's Teams page and can revoke it; see the [administration guide](admin-guide.md#extension-service-accounts). The [security model](security.md) has the details.

How extensions connect to the core is described in the [Extension Bridge](develop/extension-bridge.md).
