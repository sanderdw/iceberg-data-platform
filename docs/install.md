# Installation

Install and start Docker with Compose v2. On [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) and [Windows](https://docs.docker.com/desktop/setup/install/windows-install/), use Docker Desktop with Linux containers. Allocate at least 4 GiB of Docker memory. Images support AMD64 and ARM64.

## Install the latest release

```bash
# Linux / macOS
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh
```

```powershell
# Windows PowerShell
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

The installer does the following:

- downloads and verifies the release bundle
- creates `.env` with random credentials in `~/iceberg-data-platform` (`$HOME\iceberg-data-platform` on Windows)
- pulls the images and starts both Compose projects
- puts `iceberg_connect.py` next to `.env`, so you can [connect your own tools](../user_portal/README.md#connect-from-your-computer) such as Python, the DuckDB CLI or DBeaver
- ends with the commands to stop and start both stacks, links to the **Getting started** pages of both portals, and the bundled [agent skills](#agent-skills)

The bundled `iceberg_connect.py` uses the default local addresses. If you changed `POLARIS_PUBLIC_URL` or the Keycloak address, download it from the user portal instead (**Catalog › Connect from your computer**), which fills in your addresses.

You don't need a source checkout, Python or Node.js. Continue with the [first steps](../README.md#first-steps).

The installer scripts and archives are available in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest) for inspection.

## Agent skills

The installation folder contains two skills for coding agents in `.agents/skills`, in the open [Agent Skills](https://agentskills.io) format. Start your agent in the installation folder and ask it to use a skill, for example "Set up a demo company for my class".

- **Read `.agents/skills` by themselves:** Codex, GitHub Copilot (VS Code and CLI), Gemini CLI and Cursor.
- **Claude Code** only reads `.claude/skills`. Ask it to follow `.agents/skills/demo-company/SKILL.md`, or copy the folder to `.claude/skills`.

The skills that use the platform's MCP server explain how to connect each agent. The same steps are in the [administration guide](admin-guide.md#connect-an-mcp-client).

- [`lan-access`](../.agents/skills/lan-access/SKILL.md) makes the portals reachable from other devices on your network. It adds a Caddy HTTPS proxy on the host's LAN address, switches the sign-in addresses in `.env` and Keycloak, and moves existing users to the new address. HTTPS is required because the portals only allow plain-HTTP sign-in on `localhost`. The skill also covers firewall rules, trusting the proxy's certificate on phones and laptops, and how to undo the change. Polaris and S3 stay local-only.
- [`demo-company`](../.agents/skills/demo-company/SKILL.md) sets up a fictional Energy, Webshop or Retail company for a class or training through the [administration MCP server](admin-guide.md#connect-an-mcp-client). It asks how many participants there are (up to 48) and creates one account per participant. The accounts are spread over up to 6 teams of about 4 people, each person in one team, and each team gets its own databases. Each team has one team admin and the others are writers, so every participant can work hands-on. It ends with a numbered list of every username and one-time password to hand out. Connect the agent to the administration MCP server first; the skill explains how.

The skills are updated with the installer. Keep your own changes in a copy under another name.

## Install a specific version

Replace `latest/download` with `download/vX.Y.Z`. The bundle, Compose files and images are all pinned to that version:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.5.1/install.sh | sh
```

## Test a branch

Branches publish previews under `BRANCH-preview`. Here `BRANCH` is the branch name in lowercase, with other characters written as `-` (`feature/foo` becomes `feature-foo`). See [publishing a branch](releasing.md#publish-a-branch-for-testers).

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.sh | sh
```

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.ps1 | iex
```

Rerun the command to get a newer preview. Installations never update automatically. Previews and stable releases share project names, ports and volumes, so use a separate Docker environment to run both.

## Choose a directory

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh -s -- --dir "$HOME/my-iceberg"
```

```powershell
$env:ICEBERG_INSTALL_DIR = "$HOME\my-iceberg"
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

Use a directory outside a source checkout that Docker can access. Docker Desktop shares your home directory by default.

## Update or restart

To update, run the installer again. It keeps `.env` and the data volumes, saves the previous Compose files as `.bak`, and pulls the new images. Save your notebook work first, and read the release notes before updating.

To restart without downloading anything, run from the installation directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

## Stop and preserve data

Stop the workspaces first so the user portal can close its notebooks:

```bash
docker compose -f compose.users.yaml down
docker compose down
```

`down` keeps all data. `docker compose down --volumes` deletes the PostgreSQL, pgAdmin, RustFS and Keycloak data, so never use it during an upgrade. Team notebook files live in separate `iceberg-workspaces-work-*` volumes, and neither command removes them. Keep `.env` together with the volumes, and back both up before destructive maintenance.
