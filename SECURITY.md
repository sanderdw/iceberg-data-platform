# Security policy

This project is a local development platform. It has not undergone an independent security audit. The initial 0.2 release line receives fixes; there is no long-term support commitment.

## Reporting a vulnerability

Do not put credentials, exploitable details or private data in a public issue. Use the repository's **Security → Advisories → Report a vulnerability** form if private reporting is enabled. If it is unavailable, open an issue that only asks the maintainer to establish a private reporting channel; omit vulnerability details until that channel is available.

Include the affected version, a minimal reproduction using synthetic data, the expected authorization boundary, and the observed impact. Maintainers should enable private vulnerability reporting when creating the public repository.

## Trust boundaries

- The administration portal has one local administrator password. Database roles never grant portal-administrator access.
- The user portal authenticates existing usernames and OAuth client secrets. Its trusted directory client uses the platform identity; catalog browsing and notebook execution use the actual user's identity.
- The user gateway mounts the Docker socket and has host-level administrative capability. Run it on a dedicated, trusted development Docker host. Container isolation here is not a hardened boundary for hostile tenants.
- Notebook runtimes receive only that user's data credentials, have no Docker socket, run without root privileges or Linux capabilities, and use individual internal networks and resource limits.
- Sessions are in memory, expire after eight hours, and require one gateway replica. Restarting the gateway invalidates sessions and stops active runtimes; saved notebook files persist.
- Team switches, membership changes and catalog moves affect notebook access. Credentials already issued by external services remain subject to those services' expiration and revocation behavior.
- The active team filters the user interface. A user's underlying credentials remain valid for every team to which that user belongs.

Default ports bind to localhost. LAN overrides require an explicit host address. For access beyond a trusted local network, add HTTPS, secure cookies and an appropriate authentication/deployment design. Reverse proxies must preserve Host and support WebSockets. Do not expose the Docker socket, Polaris management API or storage administration endpoints publicly.

Keep `.env`, keys, data volumes and personal notebooks private. Database deletion removes the catalog and its stored data; it is irreversible. Routine upgrades and tests must preserve volumes.
