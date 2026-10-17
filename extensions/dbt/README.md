# dbt extension for the Iceberg Data Platform

dbt v2 pipelines for the teams of an Iceberg Data Platform installation. It is **API and MCP first**:
AI agents create projects, commit models, compile, build, test and schedule, and people watch
the pipelines in a viewer, where writers can also edit a model on a branch.

This is an extension: its own Compose project (`iceberg-dbt`) and Helm chart (`deploy/helm/iceberg-dbt`, published as `oci://ghcr.io/sanderdw/charts/iceberg-dbt`), dependencies, images, version
and releases. It uses the platform only through the **Extension Bridge v1** (`contracts/bridge` in
the platform repository), Iceberg REST and S3, so the platform and this extension upgrade independently.

## Documentation

- [dbt pipelines](https://sanderdw.github.io/iceberg-data-platform/dbt/): install, use, connect an AI agent and limits.
- [dbt pipelines internals](https://sanderdw.github.io/iceberg-data-platform/develop/dbt/): architecture, install from source and development.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
