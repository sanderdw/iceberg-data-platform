# Extension Bridge

The Bridge is the stable interface between the core platform and **extensions**: separately
versioned Compose projects or Helm charts, such as Conversational BI, that add functionality without changing the core.
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
- **Listings** (capability `listings`). A team administrator can list a data share in a data
  catalog. An extension registered with `--listing`, such as OpenMetadata, reads the listed
  shares with its service-account token: names, schemas, descriptions and semantic models,
  never data or a credential. It publishes them in its catalog so others can find the data
  product and ask the owning team for a share.
- **Team extensions** (capability `team-extensions`). An extension that a team enabled
  can ask which other extensions that team enabled, per environment. One extension serves another
  for a team only where a team administrator enabled both: Conversational BI answers the
  Evaluations extension this way, with its own automation principal.
- **App directory** (capability `app-directory`). Discovery names every registered
  extension (`title`, `purpose`) and lists the user portal's pages (`userPortalPages`), so every
  extension shows the same Apps menu: the portal's pages and the other extensions. The menu is a
  small script and stylesheet in [`v1/apps-menu/`](v1/apps-menu); copy both into the extension's page
  unchanged and call `IcebergApps.mount(element, {workspace, extensions, current})`.
- **Discovery.** `GET /bridge/v1` returns the endpoints, environments, network aliases (Docker
  aliases, or Kubernetes Services in `network.namespace`) and registered extensions, so the
  handshake file holds only the extension's identity.

## Capabilities

| Capability | Since | What it adds |
| --- | --- | --- |
| `user-context` | 1.0 | `GET /me` |
| `automation-principals` | 1.0 | Enable, list and revoke automation principals per team and environment |
| `automation-tokens` | 1.0 | Short-lived `read` or `write` catalog tokens for an automation principal. `expiresIn` is the lifetime the platform sets, one hour by default |
| `shared-data` | 1.0 | Received team shares on the read role, `sharedDatabases` in the scope, `sharedObjects` in `/me` |
| `listings` | 1.0 | `GET /listings` and `GET /listings/{listing}` for listing extensions: listed data shares with the metadata of their objects |
| `team-extensions` | 1.0 | `GET /teams/{team}/extensions` for an extension the team enabled: the extensions it enabled, per environment |
| `app-directory` | 1.0 | Discovery: `title` and `purpose` per extension, and `userPortalPages`, for the shared Apps menu ([`v1/apps-menu/`](v1/apps-menu)) |

## Conventions

The Bridge follows the [API conventions](../../docs/develop/api-conventions.md) of the extensions'
own APIs:

- Fields, query parameters and path parameters are camelCase. A path parameter is named after its
  resource: `{team}`, `{automationPrincipal}`, `{listing}`.
- Every operation has a stable camelCase `operationId`, such as `getListing`.
- Times are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z`.
- Something absent is `null`, not an empty string.
- A list is an object with one plural key, such as `{"automationPrincipals": [...]}`.
- `DELETE /automation-principals/{automationPrincipal}` answers `{"deleted": true, "automationPrincipal": "<id>"}`.

## Errors

Every response that is not a success has the same JSON body, also for a path that does not exist, a
method a path does not have and a failure the Bridge did not expect:

```json
{"error": "Only team administrators can enable an extension.", "code": "forbidden_role"}
```

Each code has one status.

`error` is text for a person and may change. `code` is stable: act on the code, never on the text.
The codes are the `enum` of `Error.code` in [`v1/openapi.yaml`](v1/openapi.yaml). A new code is a
contract change and gets an entry in [CHANGELOG.md](CHANGELOG.md); an existing code is never renamed
or given another meaning.

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_request` | 422 | The body, a query parameter or a path parameter is missing or not valid |
| `unauthorized` | 401 | No bearer token, or the token is invalid or expired. The response has `WWW-Authenticate: Bearer` |
| `unknown_extension` | 403 | The token was issued to a client that is not a registered extension |
| `not_linked` | 403 | The user's Keycloak account is not linked to a platform user, or no longer |
| `user_required` | 403 | The operation acts for a signed-in user and was called with the service account |
| `service_required` | 403 | The operation is reserved for the extension's service account and was called with a user's token |
| `listing_not_enabled` | 403 | The extension is not registered to publish listings (`--listing`) |
| `no_teams` | 403 | The user is not a member of any team any more |
| `not_a_member` | 403 | The user is not a member of the team in the path |
| `forbidden_role` | 403 | The user is a member, but only a team administrator may do this |
| `extension_not_enabled` | 403 | The team has not enabled this extension, so it may not read which extensions the team enabled |
| `forbidden` | 403 | Refused for another reason; `error` says which |
| `not_found` | 404 | The path does not exist |
| `unknown_automation_principal` | 404 | The automation principal does not exist, is being revoked, or belongs to another extension |
| `unknown_listing` | 404 | The data share does not exist or is not listed |
| `method_not_allowed` | 405 | The path exists, but not with this method. The response has `Allow` |
| `conflict` | 409 | The resource changed while the call ran, or is in a state that does not allow this. Read it again and retry |
| `internal_error` | 500 | The Bridge failed in a way it did not expect; the Bridge's log has the cause. Also the code of any status not in this table |
| `platform_error` | 502 | Polaris or the object storage refused or failed |
| `unavailable` | 503 | Polaris or the object storage cannot be reached. Try again later |

An extension that passes a Bridge error on to its own caller keeps the code as it is.

## Registering an extension

`scripts.setup --extension <id> --origin <url> --handshake <file>` registers an extension and writes
its [handshake file](v1/handshake.md). Add `--listing` for an extension that publishes listings. On
Kubernetes the platform chart's `extensions.<id>.host` registers it and writes the same keys to the
Secret `iceberg-bridge-<id>` ([network](v1/network.md#on-kubernetes)). See [extensions](../../docs/develop/extension-bridge.md#register-an-extension)
for the full steps.

See [COMPATIBILITY.md](COMPATIBILITY.md) for versioning and [CHANGELOG.md](CHANGELOG.md) for changes.
