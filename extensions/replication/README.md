# Replication (OLake) for the Iceberg Data Platform (beta)

Keep operational databases in step with a team's Apache Iceberg databases. Each run reads the
changes since the last one with [OLake](https://github.com/datazip-inc/olake-go) (change data
capture from PostgreSQL's write-ahead log, or an incremental cursor), writes them as the team's
service account and stops. People use the replications page; AI agents and scripts use the same
capabilities through MCP and a REST API. For REST APIs, files and one-time loads, use Ingestion
(dlt).

**0.0.1 is the first, beta release:** PostgreSQL only, and OLake has open issues that affect some
tables (see the known issues in [DECISIONS.md](DECISIONS.md)).

This is an extension: its own Compose project (`iceberg-replication`) and Helm chart (`deploy/helm/iceberg-replication`, published as `oci://ghcr.io/sanderdw/charts/iceberg-replication`), dependencies, images, version
and releases. It uses the platform only through the **Extension Bridge v1** (`contracts/bridge` in
the platform repository), Iceberg REST and S3, so the platform and this extension upgrade
independently.

## Documentation

- [Replication (OLake)](https://sanderdw.github.io/iceberg-data-platform/replication/): install, use, connect an AI agent and limits.
- [Replication (OLake) internals](https://sanderdw.github.io/iceberg-data-platform/develop/replication/): architecture, install from source and development.
- [Decisions](DECISIONS.md): why OLake, delete modes, time limits and what was verified.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
