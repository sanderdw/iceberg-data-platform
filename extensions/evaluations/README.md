# Evaluations extension for the Iceberg Data Platform

Question sets with known answers for the platform's AI agent (Conversational BI), scored runs,
model comparisons, schedules and live checks of real conversations. It is **API and MCP first**:
AI agents write and run question sets, and people follow the scores on a page.

A case says what a good answer is: the governed query that answers the question (computed again
at every run, so cases stay right when the data changes), an error the agent must explain, a
question back, or a polite no. Scoring is mostly code: the right rows, the right metrics and no
figure the results don't hold. An LLM judge is optional. Results go to Iceberg tables in the
team's own database, with a semantic model, so the agent's quality is a data product like any other.

This is an extension: its own Compose project (`iceberg-evaluations`) and Helm chart
(`deploy/helm/iceberg-evaluations`, published as `oci://ghcr.io/sanderdw/charts/iceberg-evaluations`),
dependencies, image, version and releases. It uses the platform only through the **Extension
Bridge v1** (contract 0.1.3 or later, `contracts/bridge` in the platform repository) and asks
Conversational BI through its evaluation API, for teams that enabled both.

## Documentation

- [Evaluations](https://sanderdw.github.io/iceberg-data-platform/evaluations/): install, use, connect an AI agent and limits.
- [Evaluations internals](https://sanderdw.github.io/iceberg-data-platform/develop/evaluations/): architecture, install from source and development.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
