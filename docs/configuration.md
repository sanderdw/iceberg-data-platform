# Configuration

With Compose, settings live in `.env` in the installation directory. After a change, restart the platform from that directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

On Kubernetes the same settings are chart values, set with `helm upgrade ... --reset-then-reuse-values --set <value>`; the last column names them. See [Install on Kubernetes](install-kubernetes.md#settings).

| Variable | Default | Purpose | Chart value |
| --- | --- | --- | --- |
| `USER_PORT` | `3002` | Host port of the user portal | (a host: `hosts.users`) |
| `PORTAL_LAN_IP` | - | Extra binding with `compose.users.lan.yaml` | - |
| `MAX_NOTEBOOKS` | `8` | Maximum simultaneous notebook containers | `notebooks.max` |
| `NOTEBOOK_MEMORY` | `2g` | Memory limit per notebook | `notebooks.memory` |
| `USER_QUERY_SLOTS` | `2` | Table previews and governed queries that run at once, for all users together | `users.queries.slots` |
| `USER_QUERY_WAIT_SECONDS` | `15` | How long an MCP preview or query waits for a free slot before it reports busy | `users.queries.waitSeconds` |
| `POLARIS_SEMANTIC_MODELS` | `true` | Set to `false` to turn off semantic models in Polaris | `polaris.semanticModels` |
| `POLARIS_TOKEN_LIFETIME` | `3600` | Lifetime in seconds of the catalog tokens of extensions and data-share recipients. Extension runs end 15 minutes before their token expires. A revoked service account or data share keeps its issued tokens up to that long | `polaris.tokenLifetime` |
| `MCP_CALLBACK_PORT` | `3010` | OAuth callback port of MCP clients | `mcpCallbackPort` |
| `PLATFORM_LISTING_EXTENSIONS` | - | Registered extensions that publish listed data shares to a data catalog, such as `openmetadata`. `scripts.setup --extension … --listing` sets it | `extensions.<id>.listing` |
| `POLARIS_PUBLIC_URL` | `http://localhost:8181` | Catalog address for `iceberg_connect.py` and external share recipients | `https://<hosts.catalog>.<baseDomain>` |
| `S3_ENDPOINT` | `http://localhost:9000` | Storage address that Polaris hands out with each table. On restart, the administration portal moves existing databases to it. | `https://<hosts.s3>.<baseDomain>` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Address of a reverse proxy, so sign-in limits apply per visitor | `forwardedAllowIps` (`*`) |

Behind a reverse proxy, enable WebSockets and preserve the Host header. See the [security model](security.md) for the isolation and trust boundaries.

Ports and Keycloak addresses are set once, before the first start (on Kubernetes `baseDomain` and `hosts`); see [Keycloak configuration](develop/keycloak.md#configuration).
