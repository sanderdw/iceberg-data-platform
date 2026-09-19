# Security policy

This project is a local development platform. It has not undergone an independent security audit. The initial 0.2 release line receives fixes; there is no long-term support commitment.

## Reporting a vulnerability

Do not put credentials, exploitable details or private data in a public issue. Use the repository's **Security → Advisories → Report a vulnerability** form if private reporting is enabled. If it is unavailable, open an issue that only asks the maintainer to establish a private reporting channel; omit vulnerability details until that channel is available.

Include the affected version, a minimal reproduction using synthetic data, the expected authorization boundary, and the observed impact. Maintainers should enable private vulnerability reporting when creating the public repository.

## Trust boundaries

- With Keycloak, the administration portal requires the `iceberg-admin/platform-admin` role. Database roles never grant portal-administrator access.
- The user portal authenticates Keycloak users linked by exact issuer and subject to a Polaris principal. Polaris itself validates Keycloak bearer tokens in mixed authentication mode. Its trusted directory client uses the platform identity; catalog browsing and notebook execution use the actual user's identity.
- Catalog details are read-only projections of user-authorized metadata. Provider configuration and credential properties are excluded. Table previews run fixed application code in resource-limited child processes with a clean environment and only the user's catalog token; storage credentials are vended by Polaris. These processes are part of the trusted gateway, not notebook containers or a hostile-code sandbox. Team/environment and catalog access are rechecked before returning preview results, including after membership revocation or sign-out during a read.
- The user gateway mounts the Docker socket and has host-level administrative capability. Run it on a dedicated, trusted development Docker host. Container isolation here is not a hardened boundary for hostile tenants.
- The internal monitoring collector also mounts the Docker socket and must be trusted with host-level capability. A read-only socket mount does not restrict Docker API methods. Its implementation only reads selected metrics, and its snapshot endpoint requires the portal secret and has no published host port. PostgreSQL collection uses read-only transactions; the default local setup reuses the database account.
- Notebook runtimes receive only that user's data credentials, have no Docker socket, run without root privileges or Linux capabilities, and use individual Docker bridge networks and resource limits. These networks allow outbound internet access; notebook ports are not published to the host.
- Sessions are in memory, expire with the Keycloak access token (15 minutes by default), and require one gateway replica. Restarting the gateway invalidates sessions and stops active runtimes; saved notebook files persist.
- Team switches, membership changes and catalog moves affect notebook access. Credentials already issued by external services remain subject to those services' expiration and revocation behavior.
- Team members share writable notebook files within each environment. They must trust each other’s code: a shared notebook executes with the credentials of the member running it. File sharing does not merge live editor state.
- The active team and environment filter the user interface and gateway access. A user's underlying credentials remain valid for every team to which that user belongs.

Default ports bind to localhost. LAN overrides require an explicit host address. For access beyond a trusted local network, add HTTPS, secure cookies and an appropriate authentication/deployment design. Reverse proxies must preserve Host and support WebSockets. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` in `.env` to the proxy's address as the portal containers see it: both portals limit sign-in attempts per client address, and without it every visitor shares the proxy's address and one limit. Never set it to `*` on a directly reachable portal, because clients could then forge `X-Forwarded-For`. Do not expose the Docker socket, Polaris management API or storage administration endpoints publicly.

Keep `.env`, keys, data volumes and team notebooks private. Database deletion removes the catalog and its stored data; it is irreversible. Routine upgrades and tests must preserve volumes.

## Keycloak lifecycle

The portal uses a dedicated realm provisioning service account. It does not receive the
master administrator password. Identity attributes are editable only by realm admins.
Revocation removes platform grants and identity links while preserving the Keycloak
account. There is no automatic token refresh or back-channel logout. Issued JWTs and
vended storage credentials can remain valid until expiry; removing an administrator
role does not immediately invalidate an existing admin session. Keycloak runs in local
development mode with a persistent development database. See [deployment boundaries](docs/keycloak.md#deployment-boundaries).
