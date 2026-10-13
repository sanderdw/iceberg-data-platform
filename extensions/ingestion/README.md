# Ingestion (dlt) for the Iceberg Data Platform

Get data in: declarative [dlt](https://dlthub.com/docs) pipelines that load REST APIs, PostgreSQL
and MySQL/MariaDB tables, and CSV, JSONL or Parquet files into a team's Apache Iceberg databases,
on demand or on a schedule. People use the pipelines page; AI agents and scripts use the same
capabilities through MCP and a REST API.

This is an extension: its own Compose project (`iceberg-ingestion`), dependencies, images, version
and releases. It uses the platform only through the **Extension Bridge v1** (`contracts/bridge` in
the platform repository), Iceberg REST and S3, so the platform and this extension upgrade
independently.

## Documentation

- [Ingestion (dlt)](https://sanderdw.github.io/iceberg-data-platform/ingestion/): install, use, connect an AI agent and limits.
- [Ingestion (dlt) internals](https://sanderdw.github.io/iceberg-data-platform/develop/ingestion/): architecture, install from source and development.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
