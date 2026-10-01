# Network contract v1

Extensions are separate Compose projects on the same Docker host.

- **Network.** Extensions join the external network named in discovery (`network.name`, default
  `iceberg-platform_default`).
- **Aliases.** The following are guaranteed on that network:

  | Alias | Service |
  | --- | --- |
  | `bridge` | Bridge API, port 3005 |
  | `polaris-control-plane` | Iceberg REST catalog, port 8181 (`catalog.internalUri`) |
  | `rustfs` | S3 storage, port 9000 (`storage.internalEndpoint`) |
  | `keycloak` | OpenID Connect provider, port 8080 (`oidc.internalIssuer`) |

- **Role labels.** The catalog and storage containers carry `io.iceberg-platform.role=catalog` and
  `io.iceberg-platform.role=storage`, and the Bridge carries `io.iceberg-platform.role=bridge`. An
  extension that connects them to a private network (for isolated jobs) looks them up by label,
  never by container name. It attaches them under the aliases above and detaches them when the job
  ends.
- **Extension labels.** Every container of an extension carries
  `io.iceberg-platform.extension=<id>`. The core monitoring shows these containers on the
  Infrastructure page without further configuration.
- **Storage endpoint.** Polaris vends the browser-facing S3 endpoint (`storage.endpoint`, for example
  `http://localhost:9000`) in table credentials. A container reaches the same storage at
  `storage.internalEndpoint`. It can keep the vended configuration unchanged by forwarding the
  vended host and port to the internal endpoint.
- **Ports.** The Bridge also listens on `127.0.0.1:3005` of the host, for extensions under
  development. Nothing else is published for extensions.
