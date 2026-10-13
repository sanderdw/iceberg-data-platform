# Agent skills

The installation folder contains five skills for coding agents in `.agents/skills`, in the open [Agent Skills](https://agentskills.io) format, and an `AGENTS.md` (with a `CLAUDE.md` pointer) that explains `iceberg_connect.py`, the MCP servers and semantic models to any agent started there. Start your agent in the installation folder and ask it to use a skill, for example "Set up a demo company for my class".

- **Read `.agents/skills` by themselves:** Codex, GitHub Copilot (VS Code and CLI), Gemini CLI and Cursor.
- **Claude Code** only reads `.claude/skills`. The installer adds a pointer there for each skill, so it finds them too.

The skills that use the platform's MCP server explain how to connect each agent. The same steps are in [AI agents](ai-agents.md) and the [administration guide](admin-guide.md#connect-an-mcp-client).

- [`quick-share`](https://github.com/sanderdw/iceberg-data-platform/blob/main/.agents/skills/quick-share/SKILL.md) makes the portals reachable from anywhere for a temporary session, such as a class with `demo-company`. It starts two free Cloudflare quick tunnels with public `trycloudflare.com` addresses and trusted certificates, so nobody installs an app or imports a certificate.
    - Participants use only the user portal address. It also carries the Iceberg catalog and S3 API, so DuckDB and PyIceberg on their own laptops work too.
    - The other address serves the administration portal and sign-in.
    - With Conversational BI installed, a third address serves the chat, linked from the user portal. The dbt and Ingestion extensions are not shared this way.
    - A gateway keeps the Keycloak administration console, the Polaris management API and the RustFS console off the internet.
    - The addresses are random, public and change whenever the tunnels restart, so end the session with the skill's undo.
- [`demo-company`](https://github.com/sanderdw/iceberg-data-platform/blob/main/.agents/skills/demo-company/SKILL.md) sets up a fictional Energy, Webshop or Retail company for a class or training through the [administration MCP server](admin-guide.md#connect-an-mcp-client). It creates one account for each of up to 48 participants in teams of about 4, each with its own databases, gives the platform administrator read-only access to every team, and ends with a printable page of sign-in slips. Connect the agent to the administration MCP server first; the skill explains how.
- [`semantic-model`](https://github.com/sanderdw/iceberg-data-platform/blob/main/.agents/skills/semantic-model/SKILL.md) creates or improves a [semantic model](semantic-models.md) for tables you can write. It first interviews you about the business questions the model must answer, profiles the data, drafts datasets, joins, metrics and AI instructions, runs every question and metric against the real tables, and publishes to Polaris only after you approve. It signs in through `iceberg_connect.py` as you.
- `dbt-platform` builds, tests and schedules dbt pipelines through the [dbt extension](dbt.md)'s MCP server. It finds the source tables with the user portal's MCP tools, works on a branch, and offers a semantic model for each finished mart. Without the dbt extension it tells you how to add it.
- `ingestion` loads data from an API, a database or files through the [Ingestion extension](ingestion.md)'s MCP server. It starts from a template, keeps secrets out of the pipeline definition, runs the load and checks the result with the user portal's MCP tools. Without the extension it tells you how to add it.

The skills are updated with the installer. Keep your own changes in a copy under another name.
