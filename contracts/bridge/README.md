# Extension Bridge

The Bridge is the stable interface between the core platform and **extension stacks**: separately
versioned Compose projects, such as the dbt stack, that add functionality without changing the core.
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
- **Discovery.** `GET /bridge/v1` returns the endpoints, environments, network aliases and
  registered extensions, so the handshake file holds only the extension's identity.

## Registering an extension

```bash
uv run python -m scripts.setup --extension dbt --origin http://localhost:3004 --handshake stacks/dbt/.env.bridge
docker compose up -d --wait        # reconciles the Keycloak clients and passes the list to the portals
```

The user portal then links to the extension, and platform administrators see and can revoke its
service accounts on the Teams page. Remove it with `--remove-extension dbt`.

See [COMPATIBILITY.md](COMPATIBILITY.md) for versioning, [CHANGELOG.md](CHANGELOG.md) for changes
and [v1/DECISIONS.md](v1/DECISIONS.md) for the evidence behind the design.
