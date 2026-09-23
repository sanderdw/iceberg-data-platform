# Security policy

This project is a local development platform. It has not had an independent security audit. Only the latest release receives fixes.

## Reporting a vulnerability

Don't put credentials, exploit details or private data in a public issue. Use **Security → Advisories → Report a vulnerability** on the repository. If that form isn't available, open an issue that only asks for a private channel. Include the affected version, a minimal reproduction with synthetic data, the expected authorization boundary and the impact you observed.

## Trust boundaries

- **Portal administration** requires the Keycloak `iceberg-admin/platform-admin` role. Team roles never grant it.
- **User portal:** users are linked by exact Keycloak issuer and subject to a Polaris principal, and Polaris validates their tokens itself. The portal's platform identity and RustFS administration credentials are used only for directory lookups and for database and share actions. Before each of those actions, the portal re-checks the caller's current Administrator role, owning team and environment against Polaris. Catalog browsing, previews and notebooks use the user's own token.
- **Previews** run fixed code in resource-limited child processes that hold only the user's token. Access is checked again before results are returned.
- **MCP endpoints** accept only RS256 Keycloak tokens issued to the public `iceberg-mcp` client. The signature, issuer, audience, expiry and authorized party are verified, and the user is mapped to their Polaris principal on every call. The admin endpoint also requires `platform-admin` on every call. Destructive tools are annotated and `delete_database` requires the exact name. `create_user` returns a one-time password in the agent's transcript, so treat that transcript as a secret.
- **Data shares** hold one read grant per selected table or view and nothing else: no listing, no writes, no other database. Vended storage credentials are read-only and limited to the shared table's location. Only a current Administrator of the owning team can manage a share, and portal administrators can only revoke. The secret is shown once and never stored. A shared view is not a row or column filter, because the recipient reads its underlying tables in full. Revocation and expiry end Polaris tokens at once, but storage credentials that were already vended remain valid until they expire. The user portal enforces expiry every 30 seconds and whenever shares are listed. Team shares grant recipients read access only, never administration.
- **Docker socket:** the user portal and the internal monitoring collector mount it, which gives them host-level capability, even with a read-only mount. Run them on a dedicated, trusted development host. Container isolation here is not a boundary against hostile tenants.
- **Notebooks** receive only their user's data credentials and their owner's current access token, never refresh tokens. They run without root or Linux capabilities, on individual networks with outbound internet access. Team members share writable notebook files, and a notebook runs with the credentials of whoever runs it, so members must trust each other's code.
- **Sessions** stay in server memory for up to eight hours, which requires one replica per portal. There is no back-channel logout. Issued JWTs and vended storage credentials stay valid until they expire. See [deployment boundaries](docs/keycloak.md#deployment-boundaries).
- The active team and environment only filter the UI. A user's credentials stay valid for all of their teams.

## Deploying beyond localhost

Default ports bind to localhost. For anything beyond a trusted local network:

- Add HTTPS and secure cookies.
- Make reverse proxies preserve the Host header and support WebSockets.
- Set `FORWARDED_ALLOW_IPS` to the proxy's address so sign-in limits apply per client. Never use `*` on a directly reachable portal.
- Never expose the Docker socket, the Polaris management API or the storage administration endpoints.
- For external data shares, put TLS in front of Polaris `/api/catalog` and the RustFS S3 API only. Set `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` before creating the shared databases.

Keep `.env`, data volumes and team notebooks private. Database deletion is irreversible, so routine upgrades and tests must preserve volumes.
