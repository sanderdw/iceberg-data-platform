# Conversational BI for the Iceberg Data Platform

Ask questions about your data in a chat. Answers come from **governed queries** on Apache Ossie
semantic models: your teams' own models, and the models that other teams **share with your team**
through data shares. The model owner decides what a metric means, how datasets join and which
fields exist; the agent only picks names. Every answer shows the query it ran, the join path and
the metric definitions, and charts load their numbers from the result, never from the LLM.

This is an extension: its own Compose project (`iceberg-conversationalbi`) and Helm chart (`deploy/helm/iceberg-conversationalbi`, published as `oci://ghcr.io/sanderdw/charts/iceberg-conversationalbi`), dependencies,
images, version and releases. It uses the platform only through the **Extension Bridge**
(`contracts/bridge` in the platform repository), Iceberg REST and S3.

## Documentation

- [Conversational BI](https://sanderdw.github.io/iceberg-data-platform/conversational-bi/): install, use, connect an AI agent and limits.
- [Conversational BI internals](https://sanderdw.github.io/iceberg-data-platform/develop/conversational-bi/): architecture, install from source and development.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
