# Handshake v1

`uv run python -m scripts.setup --extension <id> --origin <url> --handshake <file>` registers an
extension with the core. It writes one file for the extension, with mode `0600`, in `.env` format.
The extension must never commit it.

| Key | Meaning |
| --- | --- |
| `BRIDGE_CONTRACT` | Contract major version of this file: `1` |
| `BRIDGE_URL` | Bridge base URL on the platform network: `http://bridge:3005` |
| `BRIDGE_EXTENSION_ID` | The extension id, such as `dbt` |
| `BRIDGE_CLIENT_ID` | Confidential Keycloak client: `ext-<id>` (browser sign-in and service account) |
| `BRIDGE_CLIENT_SECRET` | Its secret |
| `BRIDGE_MCP_CLIENT_ID` | Public PKCE client for AI agents: `ext-<id>-mcp` |
| `OIDC_ISSUER` | Issuer as browsers see it |
| `OIDC_INTERNAL_ISSUER` | The same issuer on the platform network |
| `EXTENSION_ORIGIN` | The extension's browser origin; its sign-in callback is `<origin>/auth/callback` |
| `MCP_CALLBACK_PORT` | Loopback port MCP clients use for their OAuth callback |
| `PLATFORM_NETWORK` | Docker network to join |

Everything else (catalog, storage, environments) comes from `GET /bridge/v1`, so it is never
duplicated. Registering again keeps the secret; `--remove-extension <id>` disables the extension's
Keycloak clients on the next platform start.
