# Frequently asked questions

Short answers, each with a link to the page that has the details.

## About the platform

### Can I run it in production?

No. This is an educational local development platform. It has not had an independent security audit, only the latest release receives fixes, each portal runs as one replica, and the user portal mounts the Docker socket. Reuse the architecture and the choices behind it, not the installation itself. See the [security model](security.md) and [deploying beyond localhost](security.md#deploying-beyond-localhost).

## Install and run

### What do I need?

Docker with Compose v2 (Docker Desktop on macOS and Windows) and at least 4 GiB of Docker memory, on AMD64 or ARM64. You don't need Python, Node.js or a source checkout. See [installation](install.md).

### Where is the first password?

The installer prints it at the end. It is also `PLATFORM_ADMIN_PASSWORD` in `.env` in the installation directory. Sign in to the administration portal as `platform-admin` and choose a new password. See [first steps](first-steps.md).

### How should I get started?

The simplest start is to install only the core platform. Sign in to the administration portal as `platform-admin`. When it asks for a new password, enter the same one again, so `.env` still holds it for reference. Create a team with one Administrator in it: you. Then sign out of the administration portal, because signing in to the user portal can fail while you are still signed in there. Sign in to the user portal as your new user, create a database, open **Notebooks** and explore. You can add extensions later. See [first steps](first-steps.md).

### How do I update?

Run the installer again. It keeps `.env` and the data volumes. Save your notebook work and read the release notes first. See [update or restart](install.md#update-or-restart).

### How do I start completely fresh?

Stop every Compose project with `docker compose down --volumes`, then remove the team notebook volumes (`iceberg-workspaces-work-*`), which `down` never removes. This deletes all data and can't be undone. See [stop and preserve data](install.md#stop-and-preserve-data).

### Can people in my class use one installation from their own laptops?

Yes, with limits. The [`quick-share` skill](agent-skills.md) gives the portals public HTTPS addresses for the session through [Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/), but quick tunnels have [serious limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/#limitations). For anything more than a short session, use a stable [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/), or let everyone run their own local installation.

## Accounts and access

### I'm a team Administrator. Why can't I open the administration portal?

The administration portal needs the Keycloak role `iceberg-admin/platform-admin`. Team roles never grant it, including the Administrator role. See the [administration guide](admin-guide.md).

### What can Read, Read & write and Administrator do?

A role applies to every database of that team:

| Role          | Can                                                      |
| ------------- | -------------------------------------------------------- |
| Read          | Read namespaces, tables, views, semantic models and data |
| Read & write  | Also create and write namespaces, tables and views       |
| Administrator | Also manage catalog access, databases and data shares    |

See [teams, databases and users](admin-guide.md#teams-databases-and-users).

### A user forgot their password.

A platform administrator uses **Reset password** on the Users page, which issues a new temporary password. That works only for accounts the portal created. Reset a linked account in the Keycloak console. See the [administration guide](admin-guide.md#teams-databases-and-users).

## Notebooks and your own tools

### A package I installed is gone.

Packages you install with marimo's package installer go to `/tmp/packages` and disappear when the notebook container stops. Add permanent dependencies to the notebook image. See [notebooks](notebooks.md).

### Can two people edit the same notebook?

Teammates share the saved files, but it isn't a collaborative editor. Agree who edits a file. See [notebooks](notebooks.md).

### My DuckDB session stopped working after an hour.

The token it started with lasts one hour. From your computer, run the `iceberg_connect.py` command again. In a notebook, reconnect the DuckDB attachment or call `refresh_table_credentials`. See [connect from your computer](connect.md) and [notebooks](notebooks.md).

### `iceberg_connect.py` only works on the platform's machine.

Keycloak, Polaris and RustFS listen on `127.0.0.1`. Start the [`quick-share` skill](agent-skills.md), then download the helper again from the user portal (**Catalog › Connect from your computer**), so it has the public addresses. See [connect from your computer](connect.md).

### Why are dates and times in UTC?

Sessions run in UTC, so a date cast from a timestamp is the same on every computer. `SET TimeZone = '…'` changes it for one session. See [connect from your computer](connect.md).

## Semantic models and AI agents

### What is a semantic model, and when do I need one?

A semantic model says what the tables in a namespace mean: datasets, fields, relationships and agreed metrics. Polaris stores it next to the tables. Governed queries such as `query_semantic_model` and [Conversational BI](conversational-bi.md) need one, so an agent answers from the agreed metrics instead of writing its own SQL. See [semantic models](semantic-models.md).

### Which AI agents work?

The docs cover Claude Code, Codex and GitHub Copilot in VS Code and the CLI. Any other MCP client works if it supports streamable HTTP and OAuth with the public client ID `iceberg-mcp`. See [AI agents](ai-agents.md) and [connect an MCP client](admin-guide.md#connect-an-mcp-client).

### Does my data go to an LLM?

The core platform sends nothing to an LLM (or anything else). A coding agent you connect sees what its tools return. Conversational BI sends the conversation, the compiled SQL and at most 20 rows of each result (`BI_LLM_ROWS`) to the LLM provider you configure. To keep data on your own hardware, point `OPENAI_BASE_URL` at a local OpenAI-compatible server. See [what goes to the LLM](conversational-bi.md#what-goes-to-the-llm).

## Extensions

### How do I add an extension later?

Run the installer again and answer yes, or name the extensions in `ICEBERG_EXTENSIONS`. It keeps the extensions you already added, with their data. The installer doesn't add OpenMetadata; you [register it yourself](openmetadata.md#install). See [extensions](extensions.md#install).

### Ingestion refuses my local database.

Sources on loopback, private or link-local addresses are refused. For a demo against a database on your own computer, set `ING_ALLOW_PRIVATE_SOURCES=true` in `ingestion/.env` and restart the extension. The run then reaches your computer as `host.docker.internal`. See [isolation and limits](ingestion.md#isolation-and-limits).
