# Security policy

This project is a local development platform. It has not undergone an independent security audit. The current 0.3 release line receives fixes; there is no long-term support commitment.

## Reporting a vulnerability

Do not put credentials, exploitable details or private data in a public issue. Use the repository's **Security → Advisories → Report a vulnerability** form if private reporting is enabled. If it is unavailable, open an issue that only asks the maintainer to establish a private reporting channel; omit vulnerability details until that channel is available.

Include the affected version, a minimal reproduction using synthetic data, the expected authorization boundary, and the observed impact. Maintainers should enable private vulnerability reporting when creating the public repository.

## Trust boundaries

- Reports share query/chart definitions within a team/environment, never the author's data credentials. The gateway checks the viewer's current membership and source access before execution, cache reuse and result delivery. Definitions, SQL and parameter values are team-visible; result caches are session-scoped and transient. SQL permits a reviewed expression allowlist over one authorized `source` table and bound values. It rejects file/URL scans, extension loading, writes, introspection and dynamic table functions.
- Report workers receive a temporary viewer token, then obtain scoped table credentials from Polaris. They run as UID 10001 on internal networks containing only Polaris and RustFS, with read-only root filesystems, no notebook volumes, no Docker socket or platform/refresh credentials, bundled extensions and resource/time limits. dbt Charts receives only bounded query results and a sanitized environment; SVGs are displayed as images. The gateway-owned report-definition volume is never mounted in notebooks or query workers. Docker-host administrators remain trusted and can inspect worker configuration.
- With Keycloak, the administration portal requires the `iceberg-admin/platform-admin` role. Database roles never grant portal-administrator access.
- The user portal authenticates Keycloak users linked by exact issuer and subject to a Polaris principal. Polaris itself validates Keycloak bearer tokens in mixed authentication mode. Its trusted directory client uses the platform identity, both to read the directory and to write data shares; catalog browsing and notebook execution use the actual user's identity.
- Catalog details are read-only projections of user-authorized metadata. Provider configuration and credential properties are excluded. Table previews run fixed application code in resource-limited child processes with a clean environment and only the user's catalog token; storage credentials are vended by Polaris. These processes are part of the trusted gateway, not notebook containers or a hostile-code sandbox. Team/environment and catalog access are rechecked before returning preview results, including after membership revocation or sign-out during a read.
- A data share issues a Polaris client secret to a party outside the platform. Only a current Administrator or Database + bucket administrator of the team that owns the database can create, edit, renew or revoke one, within their active team and environment; the gateway performs these writes with the platform identity, so its own check, re-read from Polaris on every request, is the entire authorization. Portal administrators can only revoke. A share holds one read grant per selected table or view and nothing else: no listing, no writing, no other database. Storage credentials vended for it are read-only and confined to the shared table's location, which the integration check asserts against RustFS. The secret is shown once and never stored.
- A view in a data share is not a row or column filter. The recipient's engine reads the underlying tables, which must be shared too, and the recipient can read them in full.
- Revoking a share, replacing its secret or reaching its expiry ends Polaris tokens at once. Storage credentials that Polaris already vended stay valid until they expire on their own. Expiry is enforced by the user gateway every 30 seconds and whenever shares are listed; while the gateway is stopped and nobody opens the administration portal, an expired share keeps working.
- The user gateway mounts the Docker socket and has host-level administrative capability. Run it on a dedicated, trusted development Docker host. Container isolation here is not a hardened boundary for hostile tenants.
- The internal monitoring collector also mounts the Docker socket and must be trusted with host-level capability. A read-only socket mount does not restrict Docker API methods. Its implementation only reads selected metrics, and its snapshot endpoint requires the portal secret and has no published host port. PostgreSQL collection uses read-only transactions; the default local setup reuses the database account.
- Notebook runtimes receive only that user's data credentials, have no Docker socket, run without root privileges or Linux capabilities, and use individual Docker bridge networks and resource limits. These networks allow outbound internet access; notebook ports are not published to the host.
- Sessions and OIDC refresh tokens stay in server memory and require one gateway replica. Access tokens renew automatically; sessions end after at most eight hours or when Keycloak refuses renewal. Notebooks can retrieve only their owner’s current access token using their runtime credential; they never receive refresh tokens. Restarting the gateway invalidates sessions and stops active runtimes; saved notebook files persist.
- Team switches, membership changes and catalog moves affect notebook access. Credentials already issued by external services remain subject to those services' expiration and revocation behavior.
- Team members share writable notebook files within each environment. They must trust each other’s code: a shared notebook executes with the credentials of the member running it. File sharing does not merge live editor state.
- The active team and environment filter the user interface and gateway access. A user's underlying credentials remain valid for every team to which that user belongs.

Default ports bind to localhost. LAN overrides require an explicit host address. For access beyond a trusted local network, add HTTPS, secure cookies and an appropriate authentication/deployment design. Reverse proxies must preserve Host and support WebSockets. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` in `.env` to the proxy's address as the portal containers see it: both portals limit sign-in attempts per client address, and without it every visitor shares the proxy's address and one limit. Never set it to `*` on a directly reachable portal, because clients could then forge `X-Forwarded-For`. Do not expose the Docker socket, Polaris management API or storage administration endpoints publicly. Data shares for parties outside this machine require exactly such a deployment of your own: TLS in front of the Polaris catalog API (`/api/catalog` only) and the RustFS S3 API, with `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` set to those addresses before the shared databases are created. Without TLS the client secret and the data cross the network in clear text.

Keep `.env`, keys, data volumes and team notebooks private. Database deletion removes the catalog and its stored data; it is irreversible. Routine upgrades and tests must preserve volumes.

## Keycloak lifecycle

The portal uses a dedicated realm provisioning service account. It does not receive the
master administrator password. Identity attributes are editable only by realm admins.
Revocation removes platform grants and identity links while preserving the Keycloak
account. Access tokens refresh automatically, but back-channel logout is not implemented.
Issued JWTs and vended storage credentials can remain valid until expiry; administrator
roles are rechecked on token renewal, so removing a role does not immediately invalidate
an existing admin session. Keycloak runs in local
development mode with a persistent development database. See [deployment boundaries](docs/keycloak.md#deployment-boundaries).
