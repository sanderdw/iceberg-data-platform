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
- ends with where to sign in first: the administration portal, `platform-admin` and, on a new installation, the temporary password (you choose a new one at first sign-in). It also lists the commands to stop and start the platform and the bundled [agent skills](#agent-skills)

The bundled `iceberg_connect.py` uses the default local addresses. If you changed `POLARIS_PUBLIC_URL` or the Keycloak address, for example with `quick-share`, download it from the user portal instead (**Catalog › Connect from your computer**). The portal fills in your addresses.

You don't need a source checkout, Python or Node.js. Continue with the [first steps](../README.md#first-steps).

The installer scripts and archives are available in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest) for inspection.

## Add Conversational BI

The installer can also add Conversational BI (`extensions/conversationalbi` in the repository), a chat that answers questions from your teams' semantic models with an LLM. In a terminal it asks whether to add it, then which model to use and its key. At the prompt the key is never echoed and stays out of your shell history. To install without questions, pass the model and its key in the environment:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | \
  LLM_MODEL=anthropic:claude-sonnet-5-5 ANTHROPIC_API_KEY=sk-ant-... sh
```

```powershell
$env:LLM_MODEL = 'anthropic:claude-sonnet-5-5'; $env:ANTHROPIC_API_KEY = 'sk-ant-...'
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

Google Gemini (`GOOGLE_API_KEY`), Anthropic (`ANTHROPIC_API_KEY`), OpenAI (`OPENAI_API_KEY`) and Amazon Bedrock (`AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION`) are supported. The [Conversational BI configuration](https://github.com/sanderdw/iceberg-data-platform/tree/main/extensions/conversationalbi#configuration) lists the `LLM_MODEL` values and settings per provider.

`ICEBERG_EXTENSIONS=conversationalbi` adds it without a model, and `ICEBERG_EXTENSIONS=none` skips the question. Conversational BI opens on `http://localhost:3007`; a team administrator enables it once per environment with **Enable** in the chat. Allocate 8 GiB of Docker memory with it.

The model and key are in `conversationalbi/llm.env` in the installation directory, readable only by you and given only to the chat's gateway container. To change the model, edit that file and restart the gateway from the installation directory:

```bash
docker compose -f conversationalbi/compose.yaml up -d --wait
```

Rerunning the installer keeps Conversational BI and `llm.env`. Pass `LLM_MODEL` and its key again to replace them.

## Agent skills

The installation folder contains three skills for coding agents in `.agents/skills`, in the open [Agent Skills](https://agentskills.io) format, and an `AGENTS.md` (with a `CLAUDE.md` pointer) that explains `iceberg_connect.py`, the MCP servers and semantic models to any agent started there. Start your agent in the installation folder and ask it to use a skill, for example "Set up a demo company for my class".

- **Read `.agents/skills` by themselves:** Codex, GitHub Copilot (VS Code and CLI), Gemini CLI and Cursor.
- **Claude Code** only reads `.claude/skills`. The installer adds a pointer there for each skill, so it finds them too.

The skills that use the platform's MCP server explain how to connect each agent. The same steps are in the [administration guide](admin-guide.md#connect-an-mcp-client).

- [`quick-share`](../.agents/skills/quick-share/SKILL.md) makes the portals reachable from anywhere for a temporary session, such as a class with `demo-company`. It starts two free Cloudflare quick tunnels with public `trycloudflare.com` addresses and trusted certificates, so nobody installs an app or imports a certificate.
  - Participants use only the user portal address. It also carries the Iceberg catalog and S3 API, so DuckDB and PyIceberg on their own laptops work too.
  - The other address serves the administration portal and sign-in.
  - With Conversational BI installed, a third address serves the chat, linked from the user portal.
  - A gateway keeps the Keycloak administration console, the Polaris management API and the RustFS console off the internet.
  - The addresses are random, public and change whenever the tunnels restart, so end the session with the skill's undo.
- [`demo-company`](../.agents/skills/demo-company/SKILL.md) sets up a fictional Energy, Webshop or Retail company for a class or training through the [administration MCP server](admin-guide.md#connect-an-mcp-client). It creates one account for each of up to 48 participants in teams of about 4, each with its own databases, gives the platform administrator read-only access to every team, and ends with a printable page of sign-in slips. Connect the agent to the administration MCP server first; the skill explains how.
- [`semantic-model`](../.agents/skills/semantic-model/SKILL.md) creates or improves a [semantic model](../user_portal/README.md#semantic-models) for tables you can write. It first interviews you about the business questions the model must answer, profiles the data, drafts datasets, joins, metrics and AI instructions, runs every question and metric against the real tables, and publishes to Polaris only after you approve. It signs in through `iceberg_connect.py` as you.

The skills are updated with the installer. Keep your own changes in a copy under another name.

## Install a specific version

Replace `latest/download` with `download/vX.Y.Z`. The bundle, Compose files and images are all pinned to that version:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.7.0/install.sh | sh
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
docker compose -f conversationalbi/compose.yaml up -d --wait   # with Conversational BI
```

## Stop and preserve data

Stop the workspaces first so the user portal can close its notebooks:

```bash
docker compose -f conversationalbi/compose.yaml down   # with Conversational BI
docker compose -f compose.users.yaml down
docker compose down
```

`down` keeps all data. `docker compose down --volumes` deletes the PostgreSQL, pgAdmin, RustFS and Keycloak data, so never use it during an upgrade. Team notebook files live in separate `iceberg-workspaces-work-*` volumes, and neither command removes them. Keep `.env` together with the volumes, and back both up before destructive maintenance.
