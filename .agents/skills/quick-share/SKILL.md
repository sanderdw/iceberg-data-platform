---
name: quick-share
description: Make an installed Iceberg Data Platform reachable from anywhere for a temporary session, such as a class, workshop or demo, over public HTTPS addresses with trusted certificates. Uses free Cloudflare quick tunnels, so no account, domain, client app or certificate import is needed. Use when the user wants participants outside their network to open the portals for a limited time, or to end that session and undo the change.
---

# Quick share for Iceberg Data Platform

Out of the box every portal listens on `127.0.0.1` only. This skill starts two free [Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/#quick-tunnels-development), each with a random `https://<name>.trycloudflare.com` address, and points the platform at them. Browsers, phones and agents trust the certificates as they are. Nothing is rebuilt.

- **Participants** need only the user portal address. It also carries the Iceberg catalog and S3 storage, so DuckDB, PyIceberg and DBeaver on their own laptops work too.
- **The administration address** serves the administration portal and Keycloak sign-in.
- **With Conversational BI installed**, a third tunnel gives the chat its own address. Participants open it from the link in the user portal.

A small gateway in front of the tunnels routes each request and keeps internal consoles off the internet.

**Meant for temporary sessions.** Quick tunnels are free and need no Cloudflare account, but:
- The addresses are random and change every time a tunnel starts. After a restart or reboot, run this skill again; participants then use the new address.
- The addresses are public. Anyone who has one reaches the sign-in page. Cloudflare offers no access control for quick tunnels.
- Cloudflare supports quick tunnels for testing only, with no uptime guarantee and a limit of 200 concurrent requests. That is enough for a class of 48.

**What changes:**
- The administration address serves the administration portal plus Keycloak's `iceberg` realm.
  - The Keycloak administration console (`/admin`) and the `master` realm are blocked.
- The user portal address serves the user portal plus two APIs:
  - the Polaris Iceberg REST catalog (`/api/catalog`), behind Keycloak tokens;
  - the RustFS S3 API, for signed requests only.
  - The Polaris management API, the RustFS console and pgAdmin stay local-only.
- Sign-in through `http://localhost` stops working, including on the host itself, until you undo the change.
- On restart the administration portal points existing databases' storage at the new address, so databases created before the share keep working from outside.
- With Conversational BI, its address serves the chat, its API and its MCP server. `http://localhost:3007` stops working until you undo the change.

Work from the installation directory: the one holding `.env`, `compose.yaml` and `compose.users.yaml` (default `~/iceberg-data-platform`). Commands refer to this skill's files at `.agents/skills/quick-share`. Conversational BI, when installed, is in `conversationalbi` (installer) or `extensions/conversationalbi` (source checkout); `<CBI_DIR>` below.

## 1. Confirm

Tell the user that the platform will be reachable from the internet at public addresses until they undo the change, and that it is not hardened for internet exposure. Accounts need strong passwords; the one-time passwords from `demo-company` must be changed at first sign-in. With Conversational BI, every participant's question is answered by the LLM in `<CBI_DIR>/llm.env`, on the user's key and at their cost. **Get explicit confirmation** before continuing. Use your agent's question tool if it has one; otherwise ask in chat and stop until the user replies.

## 2. Preflight

1. Confirm the stack runs: `docker compose ps` lists `portal`, `keycloak` and `polaris` as healthy, and `docker compose -f compose.users.yaml ps` lists `users`. If not, start it first with the commands the installer printed.
2. Read `.env` and note the current `OIDC_ISSUER`. It is needed in step 6.
3. Check `PORTAL_ORIGIN`:
   - `http://localhost:...`: a fresh switch. Copy `.env` to `.env.before-share`. Never overwrite an existing `.env.before-share`; it holds the original localhost setup.
   - A `trycloudflare.com` address: a session is already set up. If the user wants to end it, go to "Undo". Otherwise this is a restart after the tunnels stopped; continue without a new backup.
   - Any other `https://` address: another HTTPS setup is active. Stop and ask the user to restore the localhost setup first.
4. Check for Conversational BI: `.env` has `conversationalbi=` in `PLATFORM_EXTENSIONS` and `<CBI_DIR>/.env.bridge` exists. If so, `docker compose -f <CBI_DIR>/compose.yaml ps` lists `cbi-gateway` as healthy. On a fresh switch, also copy `<CBI_DIR>/.env.bridge` to `<CBI_DIR>/.env.bridge.before-share`, again never overwriting an existing one. Without Conversational BI, skip every step about it below.

## 3. Start the tunnels

1. Start them straight from the skill folder; nothing is copied. `compose.quickshare.yaml` runs two tunnels and a gateway, plus a third tunnel with the `conversationalbi` profile. The gateway's `Caddyfile` decides which service answers each path. `--force-recreate` gives every tunnel a new address, so they always belong to the same session:
   ```sh
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml up -d --force-recreate
   ```
   With Conversational BI: `docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml --profile conversationalbi up -d --force-recreate`.
2. Read each address from the tunnel's log. It takes a few seconds to appear; retry until all are there:
   ```sh
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs admin-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs users-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml --profile conversationalbi logs cbi-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1
   ```
   PowerShell: `docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml logs admin-tunnel 2>&1 | Select-String -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1`.
   Call them `<ADMIN_URL>`, `<USERS_URL>` and `<CBI_URL>` below. If a log shows an error instead, the host cannot reach Cloudflare; outbound HTTPS and QUIC (UDP 7844) must be allowed.
3. Find the platform network's subnet. The tunnels reach the portals from an address in it:
   `docker network inspect iceberg-platform_default --format "{{(index .IPAM.Config 0).Subnet}}"`

## 4. Configure

Edit `.env`. Replace a key's existing uncommented line, or append the key when it is missing. Never add a second line for the same key. The addresses have no port:
```dotenv
PORTAL_ORIGIN=<ADMIN_URL>
KEYCLOAK_ORIGIN=<ADMIN_URL>
OIDC_ISSUER=<ADMIN_URL>/realms/iceberg
USER_ORIGIN=<USERS_URL>
POLARIS_PUBLIC_URL=<USERS_URL>
S3_ENDPOINT=<USERS_URL>
FORWARDED_ALLOW_IPS=<platform subnet from step 3>
```
- `FORWARDED_ALLOW_IPS` lets the portals trust the gateway's `X-Forwarded-*` headers, so they see HTTPS and the visitor's address.
- `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` are what tools on participants' laptops use. The user portal writes the catalog address into the `iceberg_connect.py` it serves, and Polaris hands out the storage address with each table.
- Leave `OIDC_INTERNAL_ISSUER` unchanged: containers reach Keycloak internally at `http://keycloak:8080`.

With Conversational BI, two more edits:
- In `.env`, change only the `conversationalbi=` entry of `PLATFORM_EXTENSIONS` to `conversationalbi=<CBI_URL>` and keep any other entries. Never change `PLATFORM_EXTENSION_SECRETS`. The platform registers the chat's sign-in address and the user portal's link from it.
- In `<CBI_DIR>/.env.bridge`, the same way:
  ```dotenv
  OIDC_ISSUER=<ADMIN_URL>/realms/iceberg
  EXTENSION_ORIGIN=<CBI_URL>
  ```
  The chat accepts only tokens from this issuer and sends sign-ins back to this origin. Leave `OIDC_INTERNAL_ISSUER` unchanged here too.

## 5. Restart

Run these in order. The containers are recreated because their configuration changed. The `compose.keycloak.proxy.yaml` overlay makes Keycloak trust the tunnel's `X-Forwarded-*` headers:
```sh
docker compose -f compose.yaml -f .agents/skills/quick-share/assets/compose.keycloak.proxy.yaml up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```
With Conversational BI, recreate its gateway last; the platform restart already pointed its Keycloak clients at `<CBI_URL>`:
```sh
docker compose -f <CBI_DIR>/compose.yaml up -d --wait --force-recreate cbi-gateway
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
curl -s <ADMIN_URL>/realms/iceberg/.well-known/openid-configuration
curl -s -o /dev/null -w "%{http_code}\n" <ADMIN_URL>/admin/
curl -s -o /dev/null -w "%{http_code}\n" "<USERS_URL>/api/catalog/v1/config?warehouse=check"
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" <USERS_URL>/api/management/v1/catalogs
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: AWS4-HMAC-SHA256 Credential=check" <USERS_URL>/check/check
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: AWS4-HMAC-SHA256 Credential=check" <USERS_URL>/rustfs/admin/v3/info
```
With Conversational BI:
```sh
curl -s <CBI_URL>/api/health
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" <CBI_URL>/auth/login
curl -s <CBI_URL>/.well-known/oauth-protected-resource
```
Expect:
- Both portals return `302` to `<ADMIN_URL>/realms/iceberg/protocol/openid-connect/auth?...`, with a `redirect_uri` on the same portal address.
- The discovery document's `issuer` is the new `OIDC_ISSUER`.
- `/admin/` returns `404`.
- The catalog returns `401`: Polaris answers and wants a token.
- The management API returns `401 application/json` from the user portal (`Sign in to open your workspace`), never from Polaris.
- The signed request returns `403` from RustFS (`Invalid SigV4 authorization header`).
- The signed request to the RustFS administration API returns `404` from the gateway.
- Conversational BI's health shows `"bridge":"connected"`. Its sign-in returns `303` to `<ADMIN_URL>/realms/iceberg/protocol/openid-connect/auth?...` with `client_id=ext-conversationalbi` and a `redirect_uri` on `<CBI_URL>`. The resource metadata names `<CBI_URL>/mcp` and `<ADMIN_URL>/realms/iceberg`.

A new address can take up to a minute before DNS resolves it everywhere; retry before digging deeper. If a portal restarts in a loop, run `docker compose logs portal` (or `docker compose -f compose.users.yaml logs users`, or `docker compose -f <CBI_DIR>/compose.yaml logs cbi-gateway`). "HTTP OIDC is restricted to localhost" means an origin in `.env` still starts with `http://`.

## 8. Tell the user

Summarize:
- The address to hand out: user portal `<USERS_URL>`. Participants need only this one; the sign-in page on `<ADMIN_URL>` appears by itself. Its guide at `<USERS_URL>/#guide` covers the browser, their own tools and AI agents. The administration portal is at `<ADMIN_URL>`.
- With Conversational BI: the chat is at `<CBI_URL>`, linked from the user portal. A team administrator still enables it once per environment with **Enable** in the chat. Every participant's question is billed to the LLM key in `<CBI_DIR>/llm.env`.
- If a `demo-company-*-credentials.md` file is in the installation directory, regenerate the printable sign-in slips so they carry the new address: `uv run .agents/skills/demo-company/scripts/slips.py <credentials file>`. It writes `demo-company-<domain>-slips.html` next to it, one slip per participant with the address, a QR code, username and one-time password. Tell the user it holds live passwords and should be deleted after printing.
- MCP connections from coding agents use `<ADMIN_URL>/mcp` (administration) and `<USERS_URL>/mcp` (user portal) instead of localhost. No certificate setup is needed. Replace the URL in the agent's MCP configuration and sign in again:
  - Codex: change `url` under `[mcp_servers.iceberg-admin]` in `~/.codex/config.toml`, then run `codex mcp login iceberg-admin`.
  - GitHub Copilot: change `url` in `.vscode/mcp.json` or `~/.copilot/mcp-config.json`.
  - Claude Code: `claude mcp remove iceberg-admin`, then `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin <ADMIN_URL>/mcp`.
  - Other agents: change the server URL in their MCP settings.
  - Conversational BI's MCP server moves from `http://localhost:3007/mcp` to `<CBI_URL>/mcp`, with client `ext-conversationalbi-mcp`:
    - Codex: change `url` under `[mcp_servers.conversationalbi]`, then run `codex mcp login conversationalbi`.
    - GitHub Copilot: change its `url` as above.
    - Claude Code: `claude mcp remove conversationalbi`, then `claude mcp add --transport http --client-id ext-conversationalbi-mcp --callback-port 3010 conversationalbi <CBI_URL>/mcp`.
  - These registrations stop working when the session ends; the Undo below switches them back to localhost.
- `iceberg_connect.py` has the sign-in and catalog addresses built in. Anyone who downloaded it before the share downloads it again from the user portal; the helper says so when it cannot reach an old address.
- The addresses stop working when a tunnel stops, Docker restarts or the host sleeps. Run this skill again for new addresses. Keep the host awake during the session.
- End the session with "Undo". Until then the platform stays reachable from the internet, and localhost sign-in does not work.

## Undo

1. Stop the tunnels: `docker compose -f .agents/skills/quick-share/assets/compose.quickshare.yaml --profile conversationalbi down`. The profile also removes the Conversational BI tunnel if it ran.
2. Note the current (tunnel) `OIDC_ISSUER`, then restore `.env.before-share` to `.env` and delete `.env.before-share`. With Conversational BI, also restore `<CBI_DIR>/.env.bridge.before-share` to `<CBI_DIR>/.env.bridge` and delete the backup.
3. Start without the overlay: `docker compose up -d --wait`, then `docker compose -f compose.users.yaml up -d --wait users`. With Conversational BI, then `docker compose -f <CBI_DIR>/compose.yaml up -d --wait --force-recreate cbi-gateway`.
4. Run `sync_origins.py` as in step 6:
   - `--admin-origins http://localhost:<PORT>`
   - `--user-origins http://localhost:<USER_PORT>`
   - `--old-issuer` set to the tunnel issuer from step 2.
5. Tell the user that sign-in works at `http://localhost:<PORT>` and `http://localhost:<USER_PORT>` again, and Conversational BI at `http://localhost:3007`. The restart already pointed the databases' storage back at `S3_ENDPOINT` from the restored `.env`.
6. Point coding agents back at localhost. The tunnel addresses no longer exist, so an MCP server still registered with one fails to connect (`ENOTFOUND …trycloudflare.com`). For each server the user registered during the session, `iceberg-admin` at `http://localhost:<PORT>/mcp`, `iceberg-user` at `http://localhost:<USER_PORT>/mcp` and `conversationalbi` at `http://localhost:3007/mcp`:
   - Codex: set `url` under `[mcp_servers.iceberg-admin]` (or `iceberg-user`) in `~/.codex/config.toml` back to the localhost address, then run `codex mcp login iceberg-admin`.
   - GitHub Copilot: set `url` in `.vscode/mcp.json` or `~/.copilot/mcp-config.json` back to the localhost address.
   - Claude Code: `claude mcp remove iceberg-admin`, then `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin http://localhost:<PORT>/mcp`. Do the same for `iceberg-user` with `http://localhost:<USER_PORT>/mcp`, and for `conversationalbi` with `--client-id ext-conversationalbi-mcp` and `http://localhost:3007/mcp`.
   - Other agents: change the server URL in their MCP settings.
   Then start a new agent session and sign in again.
7. `iceberg_connect.py` downloaded during the session carries the tunnel addresses. Use the copy in the installation folder, or download it again from `http://localhost:<USER_PORT>`.
