# Configuration

Settings live in `.env` in the installation directory. After a change, restart the platform from that directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_PORT` | `3002` | Host port of the user portal |
| `PORTAL_LAN_IP` | - | Extra binding with `compose.users.lan.yaml` |
| `MAX_NOTEBOOKS` | `8` | Maximum simultaneous notebook containers |
| `NOTEBOOK_MEMORY` | `2g` | Memory limit per notebook |
| `USER_QUERY_SLOTS` | `2` | Table previews and governed queries that run at once, for all users together |
| `USER_QUERY_WAIT_SECONDS` | `15` | How long an MCP preview or query waits for a free slot before it reports busy |
| `POLARIS_SEMANTIC_MODELS` | `true` | Set to `false` to turn off semantic models in Polaris |
| `POLARIS_TOKEN_LIFETIME` | `3600` | Lifetime in seconds of the catalog tokens of extensions and data-share recipients. Extension runs end 15 minutes before their token expires. A revoked service account or data share keeps its issued tokens up to that long |
| `MCP_CALLBACK_PORT` | `3010` | OAuth callback port of MCP clients |
| `PLATFORM_LISTING_EXTENSIONS` | - | Registered extensions that publish listed data shares to a data catalog, such as `openmetadata`. `scripts.setup --extension … --listing` sets it |
| `POLARIS_PUBLIC_URL` | `http://localhost:8181` | Catalog address for `iceberg_connect.py` and external share recipients |
| `S3_ENDPOINT` | `http://localhost:9000` | Storage address that Polaris hands out with each table. On restart, the administration portal moves existing databases to it. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Address of a reverse proxy, so sign-in limits apply per visitor |

Behind a reverse proxy, enable WebSockets and preserve the Host header. See the [security model](security.md) for the isolation and trust boundaries.

Ports and Keycloak addresses are set once, before the first start; see [Keycloak configuration](develop/keycloak.md#configuration).
