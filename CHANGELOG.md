# Changelog

## 0.5.1 - 2026-09-23

- **Connect from your machine:** use PyIceberg, DuckDB, the DuckDB CLI or DBeaver with your own account instead of a notebook. **Catalog › Connect from your computer** shows the snippets for each database; the `iceberg_connect.py` helper signs you in through the browser.
- **Getting started** pages in both portals show the three ways in: the portal, your own tools or scripts, and an AI agent through MCP.
- MCP works with Codex, GitHub Copilot, Cursor, Gemini CLI and other MCP clients, not only Claude Code.
- **Try it out** works for writes in the administration API documentation at `/docs`.
- The installation folder includes two agent skills: `lan-access` opens the portals to other devices on your network over HTTPS, and `demo-company` sets up a demo company with teams, databases and accounts for a class. See [agent skills](docs/install.md#agent-skills).
- Fixed: the platform administrator of an existing installation could not sign in to the administration MCP server.
- Fixed: status messages in the administration portal stayed visible on other pages.

## 0.5.0 - 2026-09-22

- Share data with another team, not only with external parties. Recipients use their own accounts and see received shares as read-only databases in Catalog and Notebooks.
- MCP servers for AI agents such as Claude Code. Users browse, describe and preview their data with their own permissions; platform administrators manage teams, databases, users and shares.
- Team administrators create, rename and delete their databases on a new **Databases** page in the user portal.
- Try the workspace API with your own session at `http://localhost:3002/docs`.
- New flight-data example notebooks for reading and writing Iceberg tables with DuckDB.
- Fixed: several open notebooks could exhaust the runtime's thread limit and stop a kernel.
- Upgrade from 0.4.1: no data migration is needed. Update the images and restart; users sign in again.

## 0.4.1 - 2026-09-21

- Notebooks export to HTML and Jupyter; PDF, thumbnail and screenshot exports are removed.
- Clearer validation and error messages for share names, account emails, names and share expiry. Shares can expire at the end of today.

## 0.4.0 - 2026-09-20

- Data shares: a team administrator shares selected tables and views with an external party, with its own credentials, an optional expiry, a new secret on demand and revocation. The recipient can read only the shared objects.
- Platform administrators see and revoke every data share on a new **Data shares** page.
- The user portal has one top menu for **Catalog**, **Notebooks** and **Data shares**.
- Both portals keep your place in the URL across reloads and Back/Forward, and lists refresh on their own.
- Sessions stay signed in for up to eight hours; notebooks get renewed tokens automatically.
- Breaking: a database with data shares can't move to another team until its shares are revoked.
- Upgrade: sharing outside this machine needs your own TLS proxy in front of Polaris and RustFS; see `SECURITY.md`.

## 0.3.1 - 2026-09-19

- Open notebooks are listed with their CPU and memory on the **Infrastructure** page.

## 0.3.0 - 2026-09-19

- Sign in to both portals, Polaris and notebooks with Keycloak. Administrators create, link, reset and revoke accounts from the portal.
- A user can have a different role in each team, managed in one **Edit access** dialog.
- Example notebooks for Iceberg v3 tables with DuckDB; table previews support v3 types such as `variant` and `geometry`.
- Install a specific version, or test a branch preview, with a one-command installer.
- Breaking: no upgrade from a password-based 0.2 installation; start from a fresh installation. The users API takes `memberships: [{team, role}]` instead of a single role.

## 0.2.1 - 2026-09-16

- One-command installers for Linux, macOS and Windows. Rerunning one keeps your credentials and data.

## 0.2.0 - 2026-09-15

Initial public release:

- Administration portal for teams, users with a role per team, and databases that can be moved and deleted, plus a catalog explorer.
- User portal with team selection, catalog browsing and persistent marimo notebooks, shared per team and environment.
- Development, Acceptance and Production environments per team.
- Container images for AMD64 and ARM64 and a Docker-only installation bundle.
