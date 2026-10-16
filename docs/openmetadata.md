# OpenMetadata

Make your data products findable. Every data share that a team administrator lists in the data catalog appears in [OpenMetadata](https://open-metadata.org/) 2.0 as a **data product**, with its tables, views, column descriptions and semantic model metrics. People find it there and ask the owning team for a data share. A listing carries metadata only: no data and no credential.

| | |
| --- | --- |
| Publishes | Listed [data shares](data-shares.md), within a minute of a change |
| Status | `http://localhost:3011/api/status`: last sync, counts and the last error code |
| Needs | An OpenMetadata 2.0 server and a bot token |

OpenMetadata 2.0 has no Iceberg connector of its own: it reaches Iceberg only through a query engine such as Trino or Snowflake, and never read semantic models. This extension pushes the listings to OpenMetadata's API instead, so no catalog credential leaves the platform.

## What is published

| Platform | OpenMetadata |
| --- | --- |
| Environment | Database service `iceberg-<environment>` |
| Database | Database, with the database name as display name |
| Namespace | Database schema |
| Listed table or view | Table (type Iceberg or View) with columns, types, descriptions and partitioning |
| Semantic model metric | Metric with its description, synonyms, AI context, SQL expression and dimensions, and lineage from the tables it reads |
| Semantic model, its datasets and described fields | Glossary terms in the `Iceberg Data Platform` glossary. Tables and columns are tagged with the terms of the datasets and fields that read them; a table's description adds what its dataset means and its AI context, and a column without a description gets its field's description, synonyms and AI context (such as examples) |
| Field label | Tag in the `Iceberg Data Platform labels` classification (named `iceberg__Iceberg Data Platform labels`), on the field's column and glossary term. Ossie calls a label a "label for categorization"; the flights example uses it for the ontology concept a field holds, such as `Delay` or `AirportCode`, so one tag finds every delay column |
| Owning team | Team, as owner |
| Listed data share | Data product in the `Iceberg Data Platform` domain, with the description, the contact for access requests, a section per semantic model (its description, AI instructions, datasets and metrics) and an ER diagram of its tables and keys. Its schemas, tables and metrics are its assets |

Never published: data, row counts, view SQL, storage locations, snapshots or credentials. A semantic model's expressions are published and can name tables that aren't listed, so check a model before you list it.

The ER diagram is drawn from the primary and foreign keys of the listed semantic models; the same keys show in each table's **Table Constraints**. Descriptions follow the platform: a description changed there replaces the one in OpenMetadata, and what people write in OpenMetadata where the platform has none stays.

Unlisting a share, revoking it or letting it expire removes it from OpenMetadata within a minute. Listing it again brings back the same tables and metrics.

## Install

The platform installer doesn't add this extension, because it needs your OpenMetadata server and a bot token. It has its own installer instead. On the machine where the platform is installed (in `~/iceberg-data-platform`, version 0.9.1 or later):

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/openmetadata-v0.1.1/install.sh | sh
```

It asks for:

- **The OpenMetadata API address**, as the extension's container reaches it, such as `https://metadata.example.com/api`.
- **A bot token**: the JWT of an OpenMetadata bot (**Settings > Bots**, for example `ingestion-bot`). The bot needs to create and delete database services, databases, schemas, tables, metrics, domains, data products, lineage, glossaries, glossary terms, classifications and tags. Without permission to create teams, listings are published without owners.
- **Where people browse OpenMetadata** (optional), such as `https://metadata.example.com`. The user portal's **OpenMetadata** link opens it.

To install without questions, pass them in the environment:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/openmetadata-v0.1.1/install.sh | \
  OM_URL=https://metadata.example.com/api OM_JWT_TOKEN=eyJ... OM_UI_URL=https://metadata.example.com sh
```

The installer:

1. Downloads the extension's files, checks them against the release's checksums and puts them in `~/iceberg-data-platform/extensions/openmetadata`.
2. Registers the extension with the platform as a listing extension and applies that to the platform services.
3. Writes the settings to `extensions/openmetadata/.env` and starts the extension.

Use `--dir` when the platform is installed elsewhere: `| sh -s -- --dir /opt/iceberg-data-platform`. Running the install command again, or that of a newer release, keeps `.env` and the registration.

### Try it with a local OpenMetadata

No OpenMetadata yet? `--demo` also starts one next to the extension, with about 4 GB of memory:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/openmetadata-v0.1.1/install.sh | sh -s -- --demo
```

The first start takes a few minutes. Open `http://localhost:8585` and sign in as `admin@open-metadata.org` with password `admin`. It's for trying the extension out, not for production. To switch to your own OpenMetadata later, remove the `COMPOSE_FILE` line from `extensions/openmetadata/.env`, set `OM_URL` and `OM_JWT_TOKEN` there, and run `docker compose up -d --wait` in that folder.

### List your first data share

1. In the user portal, a team administrator opens **Data shares** and turns on **Listed in the data catalog** on a share.
2. Within a minute it appears in OpenMetadata as a data product in the `Iceberg Data Platform` domain.
3. `http://localhost:3011/api/status` shows the last sync, its counts and the last error code.

### Change, stop or remove

From `~/iceberg-data-platform/extensions/openmetadata`:

- **Change settings:** edit `.env` (see [Settings](#settings)), then run `docker compose up -d --wait`.
- **Stop:** `docker compose down`. Add `-v` to also delete the local OpenMetadata's data.
- **Remove:** stop it, then from `~/iceberg-data-platform` unregister it and apply that:

    ```bash
    uv run python -m scripts.setup --remove-extension openmetadata
    docker compose up -d --wait && docker compose -f compose.users.yaml up -d --wait users
    ```

From a source checkout, follow the [extension's README](https://github.com/sanderdw/iceberg-data-platform/tree/main/extensions/openmetadata#install-from-source).

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `OM_URL` | | OpenMetadata API address |
| `OM_JWT_TOKEN` | | Bot JWT |
| `OM_SERVICE_PREFIX` | `iceberg` | Names this installation's services, metrics and data products; use one prefix per platform |
| `OM_DOMAIN` | `Iceberg Data Platform` | Domain of the data products, created when missing |
| `OM_POLL_SECONDS` | `60` | How often new, changed and removed listings are picked up |
| `OM_FULL_SYNC_SECONDS` | `900` | How often everything is published again, including schema and semantic model edits |
| `OM_VERIFY_TLS` | `true` | Verify OpenMetadata's certificate |
| `OM_UI_URL` | | Where people browse OpenMetadata, such as `https://metadata.example.com`. The user portal's **OpenMetadata** link opens it; without it, the link shows the status |

How it is built: [OpenMetadata internals](develop/openmetadata.md).
