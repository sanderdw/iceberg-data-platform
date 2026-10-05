# Extension Bridge

The Bridge is the stable interface between the core platform and **extensions**: separately
versioned Compose projects, such as Conversational BI, that add functionality without changing the core.
The core owns this contract. An extension depends on this contract only, never on core source code,
container names or the Polaris management API.

An extension reaches the core through four doors:

| Door | Contract |
| --- | --- |
| Bridge REST API | [`v1/openapi.yaml`](v1/openapi.yaml), served by the core `bridge` service at `http://bridge:3005/bridge/v1` |
| Iceberg REST and S3 | Standard Apache Iceberg REST (Polaris) and S3 (RustFS), with tokens issued by the Bridge |
| Network and handshake | [`v1/network.md`](v1/network.md) and [`v1/handshake.md`](v1/handshake.md) |
| Table metadata | [`v1/table-properties.md`](v1/table-properties.md): properties any producer may set and the core portal renders |

## What an extension gets

- **User context.** A user signs in to the extension through Keycloak (client `ext-<id>`, or
  `ext-<id>-mcp` for AI agents). The token's audience is `iceberg-bridge`, so Polaris refuses it.
  `GET /bridge/v1/me` returns that user's teams, roles and databases, re-checked on every call.
- **Automation principals.** A team administrator enables the extension for one environment of the
  team. The core then creates a service account holding the team's `writer` access to that
  environment's databases, and `reader` access through a second role. The extension asks for
  short-lived Polaris tokens (`read` or `write`) with its own service-account token. It never
  receives a secret or a static storage key; storage access uses Polaris's vended credentials.
- **Shared data** (capability `shared-data`). A team share that the team received in that
  environment reaches the principal's read role too. The scope lists these databases with exactly
  the tables, views and semantic models they share. Recipients can't list shared namespaces, so an
  extension loads them by name. This is how Conversational BI reads shared semantic models.
- **Discovery.** `GET /bridge/v1` returns the endpoints, environments, network aliases and
  registered extensions, so the handshake file holds only the extension's identity.

## Capabilities

| Capability | Since | What it adds |
| --- | --- | --- |
| `user-context` | 0.1 | `GET /me` |
| `automation-principals` | 0.1 | Enable, list and revoke automation principals per team and environment |
| `automation-tokens` | 0.1 | One-hour `read` or `write` catalog tokens for an automation principal |
| `shared-data` | 0.1 | Received team shares on the read role, `sharedDatabases` in the scope, `sharedObjects` in `/me` |

## Registering an extension

`scripts.setup --extension <id> --origin <url> --handshake <file>` registers an extension and writes
its [handshake file](v1/handshake.md). See [extensions](../../docs/extensions.md#register-an-extension)
for the full steps.

See [COMPATIBILITY.md](COMPATIBILITY.md) for versioning and [CHANGELOG.md](CHANGELOG.md) for changes.
