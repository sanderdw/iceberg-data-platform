---
name: quick-share
description: Make an installed Iceberg Data Platform reachable from anywhere for a temporary session, such as a class, workshop or demo, over public HTTPS addresses with trusted certificates. Uses free Cloudflare quick tunnels, so no account, domain, client app or certificate import is needed. Use when the user wants participants outside their network to open the portals for a limited time, or to end that session and undo the change.
---

# Quick share for Iceberg Data Platform

Out of the box every portal listens on `127.0.0.1` only. This skill starts three free [Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/#quick-tunnels-development) that give the administration portal, user portal and Keycloak sign-in each a random `https://<name>.trycloudflare.com` address, and points the sign-in configuration at them. Browsers, phones and agents trust the certificates as they are. Nothing is rebuilt.

**Meant for temporary sessions.** Quick tunnels are free and need no Cloudflare account, but:
- The addresses are random and change every time a tunnel starts. After a restart or reboot, run this skill again; participants then use the new addresses.
- The addresses are public. Anyone who has one reaches the sign-in page. Cloudflare offers no access control for quick tunnels.
- Cloudflare supports quick tunnels for testing only, with no uptime guarantee and a limit of 200 concurrent requests. That is enough for a class of 48.

**What changes:**
- Administration, user portal and Keycloak become three `https://<name>.trycloudflare.com` addresses.
- Sign-in through `http://localhost` stops working, including on the host itself, until you undo the change.
- The Keycloak administration console (`/admin`) is blocked on the public address. Polaris (8181), RustFS/S3 (9000) and pgAdmin (5050) stay local-only.

Work from the installation directory: the one holding `.env`, `compose.yaml` and `compose.users.yaml` (default `~/iceberg-data-platform`). Commands refer to this skill's files at `.agents/skills/quick-share`.

## 1. Confirm

Tell the user that the platform will be reachable from the internet at public addresses until they undo the change, and that it is not hardened for internet exposure. Accounts need strong passwords; the one-time passwords from `demo-company` must be changed at first sign-in. **Get explicit confirmation** before continuing. Use your agent's question tool if it has one; otherwise ask in chat and stop until the user replies.

## 2. Preflight

1. Confirm the stack runs: `docker compose ps` lists `portal`, `keycloak` and `polaris` as healthy, and `docker compose -f compose.users.yaml ps` lists `users`. If not, start it first with the commands the installer printed.
2. Read `.env` and note the current `OIDC_ISSUER`. It is needed in step 6.
3. Check `PORTAL_ORIGIN`:
   - `http://localhost:...`: a fresh switch. Copy `.env` to `.env.before-share`. Never overwrite an existing `.env.before-share`; it holds the original localhost setup.
   - A `trycloudflare.com` address: a session is already set up. If the user wants to end it, go to "Undo". Otherwise this is a restart after the tunnels stopped; continue without a new backup.
   - Any other `https://` address: another HTTPS setup is active. Stop and ask the user to restore the localhost setup first.

## 3. Start the tunnels

1. Start them straight from the skill folder; nothing is copied. `compose.quickshare.yaml` runs a tunnel per service, and a small `Caddyfile` proxy in front of Keycloak blocks its administration console. `--force-recreate` gives every tunnel a new address, so all three always belong to the same session:
   ```sh
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml up -d --force-recreate
   ```
2. Read each address from the tunnel's log. It takes a few seconds to appear; retry until all three are there:
   ```sh
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs admin-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs users-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs keycloak-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   ```
   PowerShell: `docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs admin-tunnel 2>&1 | Select-String -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1`.
   Call them `<ADMIN_URL>`, `<USERS_URL>` and `<KEYCLOAK_URL>` below. If a log shows an error instead, the host cannot reach Cloudflare; outbound HTTPS and QUIC (UDP 7844) must be allowed.
3. Find the platform network's subnet. The tunnels reach the portals from an address in it:
   `docker network inspect iceberg-platform_default --format "{{(index .IPAM.Config 0).Subnet}}"`

## 4. Configure

Edit `.env`. Replace a key's existing uncommented line, or append the key when it is missing. Never add a second line for the same key. The addresses have no port:
```dotenv
PORTAL_ORIGIN=<ADMIN_URL>
USER_ORIGIN=<USERS_URL>
KEYCLOAK_ORIGIN=<KEYCLOAK_URL>
OIDC_ISSUER=<KEYCLOAK_URL>/realms/iceberg
USER_COOKIE_SECURE=true
FORWARDED_ALLOW_IPS=<platform subnet from step 3>
```
`FORWARDED_ALLOW_IPS` lets the portals trust the tunnels' `X-Forwarded-*` headers, so they see HTTPS and the visitor's address. Leave `OIDC_INTERNAL_ISSUER` unchanged: containers reach Keycloak internally at `http://keycloak:8080`.

## 5. Restart

Run these in order. The containers are recreated because their configuration changed. The `compose.keycloak.proxy.yaml` overlay makes Keycloak trust the tunnel's `X-Forwarded-*` headers:
```sh
docker compose -f compose.yaml -f .agents/skills/quick-share/assets/compose.keycloak.proxy.yaml up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

## 6. Update Keycloak and linked users

Keycloak keeps the sign-in redirect addresses from the first start, and every linked user records the issuer they signed in with. `scripts/sync_origins.py` updates both and can be run more than once. Pipe it into the bootstrap image, which holds the needed credentials:

```sh
docker compose run --rm --no-deps -T --entrypoint /app/.venv/bin/python keycloak-bootstrap - \
  --admin-origins <ADMIN_URL> http://localhost:<PORT> \
  --user-origins <USERS_URL> http://localhost:<USER_PORT> \
  --old-issuer <OIDC_ISSUER noted in step 2> < .agents/skills/quick-share/scripts/sync_origins.py
```
`PORT` and `USER_PORT` default to 3000 and 3002. PowerShell has no `<` redirection. Use `Get-Content -Raw .agents/skills/quick-share/scripts/sync_origins.py | docker compose run ...` with the same arguments, and use a backtick instead of a backslash to continue lines.

The localhost entries only keep a later undo simple. The script prints the updated clients and the users it moved to the new issuer.

## 7. Verify

From the host. No `-k`: the certificates are trusted.
```sh
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" <ADMIN_URL>/auth/login
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" <USERS_URL>/auth/login
curl -s <KEYCLOAK_URL>/realms/iceberg/.well-known/openid-configuration
curl -s -o /dev/null -w "%{http_code}\n" <KEYCLOAK_URL>/admin/
```
Expect:
- Both portals return `302` to `<KEYCLOAK_URL>/realms/iceberg/protocol/openid-connect/auth?...`, with a `redirect_uri` on the same portal address.
- The discovery document's `issuer` is the new `OIDC_ISSUER`.
- `/admin/` returns `404`.

A new address can take up to a minute before DNS resolves it everywhere; retry before digging deeper. If a portal restarts in a loop, run `docker compose logs portal` (or `docker compose -f compose.users.yaml logs users`). "HTTP OIDC is restricted to localhost" means an origin in `.env` still starts with `http://`.

## 8. Tell the user

Summarize:
- The addresses to hand out: administration `<ADMIN_URL>`, user portal `<USERS_URL>`, and the guides at `/#guide` on each. Participants only need the user portal address; Keycloak's address appears by itself during sign-in.
- MCP connections from coding agents use `<ADMIN_URL>/mcp` (administration) and `<USERS_URL>/mcp` (user portal) instead of localhost. No certificate setup is needed. Replace the URL in the agent's MCP configuration and sign in again:
  - Codex: change `url` under `[mcp_servers.iceberg-admin]` in `~/.codex/config.toml`, then run `codex mcp login iceberg-admin`.
  - GitHub Copilot: change `url` in `.vscode/mcp.json` or `~/.copilot/mcp-config.json`.
  - Claude Code: `claude mcp remove iceberg-admin`, then `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin <ADMIN_URL>/mcp`.
  - Other agents: change the server URL in their MCP settings.
- `iceberg_connect.py` has the issuer built in. Download it again from the user portal's connection page.
- The addresses stop working when a tunnel stops, Docker restarts or the host sleeps. Run this skill again for new addresses. Keep the host awake during the session.
- End the session with "Undo". Until then the platform stays reachable from the internet, and localhost sign-in does not work.

## Undo

1. Stop the tunnels: `docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml down`.
2. Note the current (tunnel) `OIDC_ISSUER`, then restore `.env.before-share` to `.env` and delete `.env.before-share`.
3. Start without the overlay: `docker compose up -d --wait`, then `docker compose -f compose.users.yaml up -d --wait users`.
4. Run `sync_origins.py` as in step 6:
   - `--admin-origins http://localhost:<PORT>`
   - `--user-origins http://localhost:<USER_PORT>`
   - `--old-issuer` set to the tunnel issuer from step 2.
5. Tell the user that sign-in works at `http://localhost:<PORT>` and `http://localhost:<USER_PORT>` again, and that MCP clients and `iceberg_connect.py` need their localhost addresses back.
