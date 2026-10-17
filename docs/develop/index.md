# Develop

These pages are for people who build the platform or its extensions from source, or who want to know how it works inside. To install and use the platform, start at [installation](../install.md).

## Principles

Five principles behind every choice. Check a change against all five.

1. **Clean UX.** One design system. From sign-in to a notebook in a few clicks, one design language in both portals, and a Getting started page in each.
2. **API first.** The portal is just a client. Every button is a documented route with an OpenAPI schema, so a script can do everything a person can.
3. **AI native.** Agents are first-class users. Both portals serve MCP with the user's own identity, data products carry their meaning in a semantic model, and agent skills ship with the installer.
4. **Open source.** No licences. Every building block is Apache-2.0 or MIT, and so is the platform. No vendor account and no trial clock.
5. **Loosely coupled.** Open interfaces between the components: Iceberg REST, S3, OIDC and MCP, so you can replace one block without touching the others. Bigger capabilities come as extensions behind one contract, so the core stays small.

## Pages

- [Run from source](run-from-source.md): build and start both stacks from a checkout.
- [Contributing](contributing.md): development setup, tests and pull requests.
- [Architecture](architecture.md): components, authorization boundaries and the repository layout.
- [Resource model](resource-model.md): how users, teams, roles, databases and shares are stored in Polaris.
- [Keycloak](keycloak.md): realm, clients, configuration and deployment boundaries.
- [Form validation](form-validation.md): the rules both portals and the API enforce.
- [Kubernetes](kubernetes.md): the Helm charts, the Kubernetes backends and testing on kind.
- [Extension Bridge](extension-bridge.md): the contract between the core and its extensions.
- Extension internals: [Conversational BI](conversational-bi.md), [dbt pipelines](dbt.md), [Ingestion (dlt)](ingestion.md) and [OpenMetadata](openmetadata.md).
- [Publishing a release](releasing.md).
