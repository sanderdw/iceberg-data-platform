# OpenMetadata for the Iceberg Data Platform

Make data products findable: every data share that its owning team lists in the data catalog
appears in [OpenMetadata](https://open-metadata.org) as a data product, with its tables, views,
column descriptions and semantic model metrics. People find it there and ask the owning team for a
data share. A listing carries metadata only.

[`CONTEXT.md`](CONTEXT.md) defines the words this extension uses for both sides, such as listing,
label and term.

This is an extension: its own Compose project (`iceberg-openmetadata`), dependencies, image,
version and releases. It uses the platform only through the **Extension Bridge v1**
(`contracts/bridge` in the platform repository), capability `listings` (contract 0.1.1 or later).

## What is published

| Platform | OpenMetadata |
| --- | --- |
| Environment | Database service `iceberg-<environment>` (type CustomDatabase) |
| Database | Database named by its id, with the database name as display name |
| Namespace | Database schema |
| Listed table or view | Table (type Iceberg or View) with columns, types, descriptions, required columns and partitioning |
| Semantic model metric | Metric with description, synonyms, AI context, SQL expression, type and dimensions; the tables it reads as assets and lineage |
| Semantic model, its datasets and described fields | Glossary terms in the `Iceberg Data Platform` glossary; tables and columns are tagged with the terms of the datasets and fields that read them, and get their descriptions and AI context where the platform has none |
| Field label | Tag in the `Iceberg Data Platform labels` classification, on the field's column and glossary term |
| Owning team | Team, as owner |
| Listed data share | Data product in the `Iceberg Data Platform` domain, with the contact for access requests, a section per semantic model (description, AI instructions, datasets, metrics), an ER diagram of its tables and keys, and its schemas, tables and metrics as assets |

**Never published:** data, row counts, view SQL, storage locations, snapshots or credentials. A
semantic model's metric and dimension expressions are published, and its datasets can name tables
that are not listed; the owning team decides by listing the model.

Unlisting, revoking or an expiry removes the listing within a minute: tables and metrics are
soft-deleted and the data product is deleted. Listing it again brings back the same entities.

## Install from source

You need a running platform (core 0.9.1 or later) and an OpenMetadata 2.0 server.

1. From the platform directory, register the extension as a listing extension and recreate the
   platform services:

   ```bash
   uv run python -m scripts.setup --extension openmetadata --origin http://localhost:3011 \
     --handshake extensions/openmetadata/.env.bridge --listing
   docker compose up -d --wait && docker compose -f compose.users.yaml up -d --wait users
   ```

2. In `extensions/openmetadata`, create `.env` and set the OpenMetadata address and a bot token:

   ```bash
   uv run python -m scripts.setup
   ```

   - `OM_URL`: the OpenMetadata API as the container reaches it, such as
     `https://metadata.example.com/api`.
   - `OM_JWT_TOKEN`: the JWT of an OpenMetadata bot (**Settings > Bots**, for example
     `ingestion-bot`). It needs to create and delete database services, databases, schemas, tables,
     metrics, domains and data products, and lineage. Without permission to create teams, listings
     are published without owners.

3. Start it:

   ```bash
   docker compose up -d --build --wait
   ```

`http://localhost:3011/api/status` shows the last sync, counts and the last error code.

### A local OpenMetadata for a demo

`compose.openmetadata.yaml` adds an OpenMetadata 2.0.5 server (with PostgreSQL and
Elasticsearch, about 4 GB of memory) to the same project:

```bash
docker compose -f compose.yaml -f compose.openmetadata.yaml up -d --wait openmetadata-server
uv run python -m scripts.om_token
docker compose -f compose.yaml -f compose.openmetadata.yaml up -d --build --wait
```

Open `http://localhost:8585` and sign in as `admin@open-metadata.org` with password `admin`.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `OM_URL` | | OpenMetadata API address |
| `OM_JWT_TOKEN` | | Bot JWT |
| `OM_SERVICE_PREFIX` | `iceberg` | Names this installation's services, metrics and data products; use one per platform |
| `OM_DOMAIN` | `Iceberg Data Platform` | Domain of the data products, created when missing |
| `OM_POLL_SECONDS` | `60` | How often new, changed and removed listings are picked up |
| `OM_FULL_SYNC_SECONDS` | `900` | How often everything is published again, including schema and semantic model edits |
| `OM_VERIFY_TLS` | `true` | Verify OpenMetadata's certificate |
| `OM_UI_URL` | | Where people browse OpenMetadata, such as `https://metadata.example.com`. The user portal's **OpenMetadata** link opens it; without it, the link shows the status |

## Development

```bash
uv sync && uv run pytest && uv run ruff check . && uv run python -m scripts.release
```

See [AGENTS.md](AGENTS.md), [DECISIONS.md](DECISIONS.md) and the [changelog](CHANGELOG.md).

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
