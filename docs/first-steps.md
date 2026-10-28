# First steps

In this tutorial you take a fresh installation from its first sign-in to an answer about your own data. As the platform administrator you create a team and its first member. Then you work as that member: you create a database for the team, write an Iceberg table in a notebook, find it in the catalog and ask an AI agent about it. It takes about fifteen minutes.

You need a running [Compose installation](install.md). On Kubernetes, use `https://admin.<domain>` and `https://users.<domain>` wherever this page says `http://localhost:3000` and `http://localhost:3002`, and read the one-time password from the platform Secret; see [addresses and sign-in](install-kubernetes.md#addresses-and-sign-in).

## 1. Sign in as the platform administrator

Open the administration portal at http://localhost:3000 and sign in as `platform-admin` with the temporary password. The installer printed it, and it is `PLATFORM_ADMIN_PASSWORD` in `.env` in the installation directory. Keycloak asks for a new password; entering the same one again keeps `.env` as a reference.

You see the portal's overview: no teams, no databases and no users yet.

## 2. Create a team and its first member

1. On **Teams**, create a team named `analytics`.
2. On **Users**, choose **Create Keycloak account**. Enter a username such as `alex`, a first and last name and an email address. Select the team `analytics` with the role **Administrator**.
3. Copy the temporary password. The portal shows it only once.

The Users page now lists `alex` as an Administrator of `analytics`. That is all the platform administrator does: from here the team creates its own databases and manages its data shares. Teammates who only analyze data get **Read & write** or **Read** instead.

## 3. Sign in as the team member

Sign out of the administration portal first: signing in to the user portal can fail while you are still signed in there.

Open the user portal at http://localhost:3002 and sign in as `alex` with the temporary password, then choose a new one. Check that **Active team** is `analytics` and **Environment** is Development.

**Team overview** shows Alex as the team's only member. The team has no databases yet.

## 4. Create a database

1. Open **Databases** and choose **Create database**.
2. Name it `sandbox` and create it.

The database is created for the active team and environment: `analytics`, in Development. It is an Iceberg catalog with its own storage bucket, and every member of `analytics` gets access with their role in the team.

## 5. Create a table in a notebook

1. Open **Notebooks**. Under **Open a database**, choose `sandbox`. The portal starts your own marimo container, which takes a moment the first time.
2. In the **Notebook** selector, pick **01 · Neighborhood data with PyIceberg**.

marimo runs the notebook from top to bottom; run a cell again with ▶. The notebook generates synthetic electricity data, writes it to the table `neighborhood_electricity` in the namespace `synthetic`, and reads it back. The last section shows the stored rows.

The notebook signed in as Alex. It could write because Alex's role in the team that owns `sandbox` includes writing.

## 6. Find the table in the catalog

1. Open **Catalog** and select `sandbox`. Refresh the catalog if it doesn't list `synthetic` yet.
2. Open `synthetic` and choose **Details →** next to `neighborhood_electricity`.

You see the table's schema, its first snapshot and its properties. Choose **Load preview** to read up to 100 rows. Everything here runs with Alex's own permissions.

## 7. Ask an AI agent

Register the user portal's MCP server in Claude Code:

```bash
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://localhost:3002/mcp
```

Start Claude Code and ask: "Which tables are in my sandbox database, and what does neighborhood_electricity hold?"

On the first tool call, Claude Code opens Keycloak in your browser. Sign in as `alex`. The agent lists the database, describes the table and previews its rows, with the same permissions Alex has in the portal. [AI agents](ai-agents.md) shows the same setup for Codex and GitHub Copilot.

## What you did

- As the platform administrator you created a team and made one member its Administrator.
- As that member you created the team's database, wrote an Iceberg table, browsed it in the catalog and queried it through an agent. The notebook, the catalog and the agent all used the same Keycloak identity and the same grants.

[Teams, environments and roles](teams-and-environments.md) explains why access works this way.

## Services

Every address, with its sign-in, is listed in the [services reference](reference/services.md).

## Next

- Try the other [example notebooks](notebooks.md). Notebook **06** publishes a data product with its semantic model.
- [Connect your own tools](connect.md), such as Python, the DuckDB CLI or DBeaver.
- [Build a semantic model](build-a-semantic-model.md), so agents answer from agreed metrics.
- [Run a class](run-a-class.md) on a Kubernetes installation.
- Add [extensions](extensions.md): Conversational BI, dbt pipelines, Ingestion (dlt) and OpenMetadata.
- Stuck on something? Check the [FAQ](faq.md).
