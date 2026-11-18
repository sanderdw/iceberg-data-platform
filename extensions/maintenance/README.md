# Maintenance for the Iceberg Data Platform (beta)

Day-2 operations for a team's Apache Iceberg tables: expire old snapshots, compact small data files
(applying their deletes), remove dangling delete files, merge manifests and remove orphan files. A
team administrator sets one policy per database, with an optional schedule; people use the table
health page, and AI agents and scripts use the same capabilities through MCP and a REST API.

**0.0.1 is the first, beta release.** The runner pins a `main` commit of
[Apache iceberg-go](https://github.com/apache/iceberg-go), whose maintenance actions are not all
released yet. The platform's installer adds it with Docker Compose (`ICEBERG_EXTENSIONS=maintenance`), and on
Kubernetes it has its own Helm chart. See [DECISIONS.md](DECISIONS.md).

This is an extension: its own Compose project (`iceberg-maintenance`) and Helm chart
(`deploy/helm/iceberg-maintenance`, published as `oci://ghcr.io/sanderdw/charts/iceberg-maintenance`),
dependencies, images, version and releases. It uses the platform only through the **Extension Bridge v1** (`contracts/bridge` in the
platform repository), Iceberg REST and S3, so the platform and this extension upgrade independently.

| Part | Technology |
| --- | --- |
| Gateway: page, REST API, MCP server, policies, schedules | Python, FastAPI (`maintenance/`) |
| Launcher: the only component with the Docker socket, or on Kubernetes the right to create Jobs | Python (`launcher/`) |
| Runner: one isolated container or Kubernetes Job per run | Go, Apache iceberg-go (`runner/`) |

## Documentation

- [Maintenance](https://sanderdw.github.io/iceberg-data-platform/maintenance/): install, use, connect an AI agent and limits.
- [Maintenance internals](https://sanderdw.github.io/iceberg-data-platform/develop/maintenance/): architecture, install from source and development.
- [Decisions](DECISIONS.md) and [changelog](CHANGELOG.md)

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
