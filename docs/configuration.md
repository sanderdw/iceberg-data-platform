# Configuration

## Compose

With Compose, settings live in `.env` in the installation directory. After a change, restart the platform from that directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

On Kubernetes the same settings are chart values; the last column names them.

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
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP/HTTP receiver for logs and traces; see [OpenTelemetry](observability.md) | `otel.endpoint` |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | Headers for that receiver, such as its API key | `otel.headersSecret` |
| `OTEL_RESOURCE_ATTRIBUTES` | - | Added to every record, such as `deployment.environment.name=production` | `otel.resourceAttributes` |
| `POLARIS_OTEL_DISABLED` | `true` | Set to `false` to send Polaris's traces too | `otel.polaris` (`true`) |

Behind a reverse proxy, enable WebSockets and preserve the Host header. See the [security model](security.md) for the isolation and trust boundaries.

Ports and Keycloak addresses are set once, before the first start (on Kubernetes `baseDomain` and `hosts`); see [Keycloak configuration](develop/keycloak.md#configuration).

## Kubernetes

Chart values are set with `helm upgrade ... --reset-then-reuse-values --set <value>`; see [Install on Kubernetes](install-kubernetes.md#settings). `helm show values oci://ghcr.io/sanderdw/charts/iceberg-data-platform` lists every setting with its meaning. The ones you are most likely to change:

| Setting | Default | Meaning |
| --- | --- | --- |
| `baseDomain` | (required) | Each portal gets `<host>.<baseDomain>` |
| `hosts.admin`, `hosts.users`, … | `admin`, `users`, `auth`, `catalog`, `s3` | The host names under the base domain |
| `ingress.className` | cluster default | Your IngressClass, such as `traefik` |
| `tls.clusterIssuer` | (none) | The cert-manager ClusterIssuer; without it, provide the Secret `tls.secretName` yourself |
| `storageClass` | cluster default | For Postgres, RustFS and notebook storage |
| `polaris.external.enabled` | off | Polaris from the official Apache Polaris chart; see [use the official Apache Polaris chart](install-kubernetes.md#use-the-official-apache-polaris-chart) |
| `postgres.external.enabled`, `.host`, `.existingSecret` | off | Your own PostgreSQL instead of the bundled one; see [use a managed PostgreSQL](install-kubernetes.md#use-a-managed-postgresql) |
| `postgres.storage`, `rustfs.storage` | `10Gi`, `50Gi` | Volume sizes (`postgres.storage` only for the bundled Postgres) |
| `notebooks.max`, `notebooks.memory`, `notebooks.storage` | `8`, `2g`, `5Gi` | Notebooks at once, memory per notebook, files per team and environment |
| `polaris.tokenLifetime` | `3600` | Seconds that catalog tokens and storage credentials stay valid (`POLARIS_TOKEN_LIFETIME` on Compose) |
| `polaris.semanticModels` | `true` | Semantic models in Polaris (`POLARIS_SEMANTIC_MODELS`) |
| `users.queries.slots`, `users.queries.waitSeconds` | `2`, `15` | Previews and governed queries at once, and how long MCP waits for a slot (`USER_QUERY_SLOTS`, `USER_QUERY_WAIT_SECONDS`) |
| `notebooks.startSeconds` | `180` | How long a notebook may take to start; the first on a node pulls its image |
| `mcpCallbackPort` | `3010` | The loopback port MCP clients use for their sign-in callback (`MCP_CALLBACK_PORT`) |
| `forwardedAllowIps` | `*` | Addresses whose `X-Forwarded-For` the portals trust; see the [security model](security.md#on-kubernetes) |
| `keycloak.adminOrigin` | `http://localhost:8080` | The address of the Keycloak administration console, through a port-forward |
| `keycloak.proxyTrustedAddresses` | (all) | Addresses Keycloak takes `X-Forwarded-*` from |
| `networkPolicy.enabled`, `networkPolicy.privateRanges` | on, private ranges | The NetworkPolicies, and the ranges notebooks may not reach |
| `otel.endpoint`, `otel.headersSecret` | (none) | Send logs and traces to your OpenTelemetry backend; see [OpenTelemetry](observability.md#kubernetes) |
| `extensions.<id>.host`, `.origin`, `.listing` | (none) | Registers an extension; see [add extensions](install-kubernetes.md#add-extensions) |
| `existingSecret` | (none) | Your own platform Secret, for GitOps tools |
| `imagePullSecrets` | (none) | For a mirror of the images in a private registry |
| `demo` | `false` | Demo accounts and data, as in a demo installation |
