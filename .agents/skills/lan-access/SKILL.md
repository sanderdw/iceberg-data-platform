---
name: lan-access
description: Make an installed Iceberg Data Platform reachable from other devices on the local network (laptops, phones, colleagues) over HTTPS. Use when the user wants to open the administration portal, user portal or Keycloak sign-in from another machine, share the platform on their LAN or office network, or undo that change.
---

# LAN access for Iceberg Data Platform

Out of the box every portal listens on `127.0.0.1` only. This skill puts a Caddy HTTPS proxy on the host's LAN address and points the sign-in configuration at it. Nothing is rebuilt.

**Why HTTPS:** the portals refuse plain-HTTP sign-in on any address other than `localhost`, because session cookies and OIDC redirects must not travel unencrypted over a shared network. Caddy issues certificates from its own local certificate authority, so no domain or internet access is needed.

**What changes:**
- Administration, user portal and Keycloak become `https://<LAN_IP>:3000`, `:3002` and `:8080`.
- Sign-in through `http://localhost` stops working, including on the host itself. Use the HTTPS addresses everywhere.
- Polaris (8181), RustFS/S3 (9000) and pgAdmin (5050) stay local-only.

Work from the installation directory: the one holding `.env`, `compose.yaml` and `compose.users.yaml` (default `~/iceberg-data-platform`). `SKILL_DIR` below means the directory of this file, `.agents/skills/lan-access`. Show the user each command before running anything that needs `sudo` or administrator rights.

## 1. Preflight

1. Confirm the stack runs: `docker compose ps` lists `portal`, `keycloak` and `polaris` as healthy, and `docker compose -f compose.users.yaml ps` lists `users`. If not, start it first with the commands the installer printed.
2. Read `.env` and note the current values of `OIDC_ISSUER`, `PORTAL_ORIGIN`, `USER_ORIGIN`, `KEYCLOAK_ORIGIN`, and `PORT`/`USER_PORT`/`KEYCLOAK_PORT` (defaults 3000/3002/8080). The old `OIDC_ISSUER` is needed in step 5.
3. If `PORTAL_ORIGIN` already starts with `https://`, LAN access is already set up. Ask whether the user wants to change the address (continue with the new IP) or undo it (go to "Undo").
4. Back up the configuration: copy `.env` to `.env.before-lan`. Never overwrite an existing `.env.before-lan`; it holds the original localhost setup.

## 2. Choose the address

Detect the host's LAN IPv4 address, then **confirm it with the user**. Use your agent's question tool if it has one; otherwise ask in chat and stop until the user replies.
- Linux: `ip -4 route get 1.1.1.1` (the `src` value)
- macOS: `ipconfig getifaddr en0` (try `en1` for some Wi-Fi setups)
- Windows (PowerShell): `(Get-NetIPConfiguration | Where-Object IPv4DefaultGateway).IPv4Address.IPAddress`
- WSL2: the Windows host's address, taken from PowerShell on Windows, not the WSL `eth0` address. With Docker Desktop that is enough. With Docker Engine installed inside WSL, also enable mirrored networking (`networkingMode=mirrored` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`), or other devices cannot reach it.

Advise the user to reserve this address for the host in their router's DHCP settings. The address is part of every sign-in URL; if it changes, this skill must run again.

Use private addresses only (10.x, 172.16–31.x, 192.168.x). For access from outside the network, recommend a VPN such as Tailscale or WireGuard instead of port forwarding. The platform is not hardened for exposure to the internet.

## 3. Configure

1. Copy `SKILL_DIR/assets/Caddyfile`, `SKILL_DIR/assets/compose.proxy.yaml` and `SKILL_DIR/assets/compose.keycloak.lan.yaml` into the installation directory. Keep existing copies if they are identical.
2. Find the platform network's subnet. The proxy reaches the portals from an address in it:
   `docker network inspect iceberg-platform_default --format "{{(index .IPAM.Config 0).Subnet}}"`
3. Edit `.env`. Replace a key's existing uncommented line, or append the key when it is missing. Never add a second line for the same key. Keep ports as configured:
   ```dotenv
   PORTAL_LAN_IP=<LAN_IP>
   PORTAL_ORIGIN=https://<LAN_IP>:<PORT>
   USER_ORIGIN=https://<LAN_IP>:<USER_PORT>
   KEYCLOAK_ORIGIN=https://<LAN_IP>:<KEYCLOAK_PORT>
   OIDC_ISSUER=https://<LAN_IP>:<KEYCLOAK_PORT>/realms/iceberg
   USER_COOKIE_SECURE=true
   FORWARDED_ALLOW_IPS=<platform subnet from step 2>
   ```
   `FORWARDED_ALLOW_IPS` lets the portals trust the proxy's `X-Forwarded-*` headers, so they see HTTPS and the visitor's address. Leave `OIDC_INTERNAL_ISSUER` unchanged: containers reach Keycloak internally at `http://keycloak:8080`.

## 4. Restart

Run these in order. The containers are recreated because their configuration changed:
```sh
docker compose -f compose.yaml -f compose.keycloak.lan.yaml up -d --wait
docker compose -f compose.users.yaml up -d --wait users
docker compose -f compose.proxy.yaml up -d
```
If the proxy fails with "address already in use", another service holds one of the ports on that address.

## 5. Update Keycloak and linked users

Keycloak keeps the sign-in redirect addresses from the first start, and every linked user records the issuer they signed in with. `scripts/lan_sync.py` updates both and can be run more than once. Pipe it into the bootstrap image, which holds the needed credentials:

```sh
docker compose run --rm --no-deps -T --entrypoint /app/.venv/bin/python keycloak-bootstrap - \
  --admin-origins https://<LAN_IP>:<PORT> http://localhost:<PORT> \
  --user-origins https://<LAN_IP>:<USER_PORT> http://localhost:<USER_PORT> \
  --old-issuer <old OIDC_ISSUER> < .agents/skills/lan-access/scripts/lan_sync.py
```
PowerShell has no `<` redirection. Use `Get-Content -Raw .agents/skills/lan-access/scripts/lan_sync.py | docker compose run ...` with the same arguments, and use a backtick instead of a backslash to continue lines.

The first origin of each list must be the new `PORTAL_ORIGIN` / `USER_ORIGIN`. The localhost entries only keep a later undo simple. The script prints the updated clients and the users it moved to the new issuer.

## 6. Verify

From the host (`-k` because the certificate is not trusted yet):
```sh
curl -sk -o /dev/null -w "%{http_code} %{redirect_url}\n" https://<LAN_IP>:<PORT>/auth/login
curl -sk -o /dev/null -w "%{http_code} %{redirect_url}\n" https://<LAN_IP>:<USER_PORT>/auth/login
curl -sk https://<LAN_IP>:<KEYCLOAK_PORT>/realms/iceberg/.well-known/openid-configuration
```
Expect:
- Both portals return `302` to `https://<LAN_IP>:<KEYCLOAK_PORT>/realms/iceberg/protocol/openid-connect/auth?...`, with a `redirect_uri` on the same HTTPS origin.
- The discovery document's `issuer` is the new `OIDC_ISSUER`.

If a portal restarts in a loop, run `docker compose logs portal` (or `docker compose -f compose.users.yaml logs users`). "HTTP OIDC is restricted to localhost" means an origin in `.env` still starts with `http://`.

## 7. Firewall and certificate trust

Open the three ports to the local network only:
- Linux with ufw: `sudo ufw allow from <LAN subnet, e.g. the /24 of LAN_IP> to any port 3000,3002,8080 proto tcp`
- Linux with firewalld: `sudo firewall-cmd --permanent --add-port={3000,3002,8080}/tcp && sudo firewall-cmd --reload`
- Windows (administrator PowerShell), with the network profile set to Private: `New-NetFirewallRule -DisplayName "Iceberg Data Platform LAN" -Direction Inbound -Protocol TCP -LocalPort 3000,3002,8080 -Action Allow -Profile Private`
- macOS: allow incoming connections for Docker when prompted (System Settings > Network > Firewall).

Export Caddy's root certificate and have users trust it on each device:
```sh
docker compose -f compose.proxy.yaml cp caddy:/data/caddy/pki/authorities/local/root.crt ./iceberg-lan-root.crt
```
Until they trust it, browsers show a warning that must be accepted separately for all three ports, including 8080 during sign-in. How to trust it:
- macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain iceberg-lan-root.crt`
- Windows (administrator): `Import-Certificate -FilePath iceberg-lan-root.crt -CertStoreLocation Cert:\LocalMachine\Root`
- Debian/Ubuntu: copy it to `/usr/local/share/ca-certificates/iceberg-lan-root.crt`, then run `sudo update-ca-certificates`. Firefox uses its own store: Settings > Certificates > Import.
- iPhone/iPad: install the file as a profile, then enable it under Settings > General > About > Certificate Trust Settings.
- Android: Settings > Security > Encryption & credentials > Install a certificate > CA certificate.

The certificate stays valid across restarts because it lives in the `caddy-data` volume.

## 8. Tell the user

Summarize:
- The new addresses: administration `https://<LAN_IP>:<PORT>`, user portal `https://<LAN_IP>:<USER_PORT>`, and the guides at `/#guide` on each.
- The new start and stop commands, which replace the ones the installer printed:
  ```sh
  # Start
  docker compose -f compose.yaml -f compose.keycloak.lan.yaml up -d --wait && docker compose -f compose.users.yaml up -d --wait users && docker compose -f compose.proxy.yaml up -d
  # Stop
  docker compose -f compose.proxy.yaml down && docker compose -f compose.users.yaml down && docker compose down
  ```
  On PowerShell, use `;` instead of `&&`. After rerunning the installer for an upgrade, run the start command again so Keycloak gets its proxy setting back.
- MCP connections from coding agents use `https://<LAN_IP>:<PORT>/mcp` (administration) and `https://<LAN_IP>:<USER_PORT>/mcp` (user portal) instead of localhost. The agent still runs on its own machine and signs in through a local callback, so nothing else changes. Replace the URL in the agent's MCP configuration and sign in again:
  - Codex: change `url` under `[mcp_servers.iceberg-admin]` in `~/.codex/config.toml`, then run `codex mcp login iceberg-admin`.
  - GitHub Copilot: change `url` in `.vscode/mcp.json` or `~/.copilot/mcp-config.json`.
  - Claude Code: `claude mcp remove iceberg-admin`, then `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin https://<LAN_IP>:<PORT>/mcp`.
  - Other agents: change the server URL in their MCP settings.
- Agents must trust the root certificate too. Trusting it in the operating system (step 7) covers browsers and VS Code. CLIs built on Node.js, such as Claude Code, Copilot CLI and Gemini CLI, also need `NODE_EXTRA_CA_CERTS=/path/to/iceberg-lan-root.crt` in their environment.
- `iceberg_connect.py` has the issuer built in. Download it again from the user portal's connection page.
- Where the backup is (`.env.before-lan`) and that the user can ask you to undo the change.

## Undo

1. Stop the proxy: `docker compose -f compose.proxy.yaml down`.
2. Note the current (LAN) `OIDC_ISSUER`, then restore `.env.before-lan` to `.env`.
3. Start without the overlay: `docker compose up -d --wait`, then `docker compose -f compose.users.yaml up -d --wait users`.
4. Run `lan_sync.py` as in step 5:
   - `--admin-origins http://localhost:<PORT>`
   - `--user-origins http://localhost:<USER_PORT>`
   - `--old-issuer` set to the LAN issuer from step 2.
5. Remove the firewall rules you added. Users can delete the trusted root certificate from their devices.

## Change the address later

Rerun steps 2–6. The `--old-issuer` is the previous LAN issuer, which is still in `.env` before you edit it. Keep `.env.before-lan` as it is.
