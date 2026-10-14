# Changelog

## 0.9.1 - 2026-10-13

- **List a data product in your data catalog.** A data share can now also be **Listed in the data catalog**: its tables, views, columns, descriptions and semantic models are published without data or a credential, so others can find it and ask you for access. Team administrators switch a listing on or off at any time, with a contact for access requests. See [data shares](data-shares.md#what-a-listing-publishes).
- **OpenMetadata**, a new extension, publishes listed shares to [OpenMetadata](https://open-metadata.org/) 2.0: tables and views with their columns, semantic model metrics with their dimensions, lineage, and a data product per share. Semantic models also become glossary terms, and field labels tags. See [OpenMetadata](openmetadata.md).
- **An AI-ready grid forecast with Polars.** Example notebooks 09 and 10 write and read a synthetic medium-voltage load forecast and congestion outlook: Polars generates it lazily, checks it with SQL and writes it with its own Iceberg sink, and the semantic model's metrics run unchanged in Polars and in governed queries. Use `polars_storage_options()` to read or write Iceberg tables with Polars in your own notebooks.
- **Flights example fields say what they mean.** Notebook 06 gives every field of the flights semantic model a description, and a label for the ontology concept it holds, with synonyms and examples where they help. The catalog, AI agents and data catalogs show them.
- The Extension Bridge contract is now 0.1.1, with the `listings` capability. Existing extensions keep working.
- The [presentation](https://sanderdw.github.io/iceberg-data-platform/presentation/) covers all four extensions, data catalog listings with OpenMetadata, and the current component versions.

## 0.9.0 - 2026-10-13

- **dbt pipelines**, the second extension: your AI agent builds, tests and schedules dbt models for your team. Writers work on a branch and build it in development; team administrators merge to `main`, which acceptance and production build. See [dbt pipelines](dbt.md).
- **Follow and edit pipelines in the dbt viewer**: every model's status and tests per environment and branch, live builds, lineage, row previews, file editing and schedules. From your terminal, `iceberg_dbt.py build`, `test` and `show` run on the platform and stream the log back.
- **Ingestion (dlt)**, the third extension: load REST APIs, PostgreSQL, MySQL or MariaDB tables, and CSV, JSONL or Parquet files into your team's databases, on demand or on a schedule. Team administrators set up pipelines and their secrets, which are encrypted and write-only; writers start runs. See [Ingestion (dlt)](ingestion.md).
- **Load data with dlt in a notebook.** Example 08 loads paged API data with your own identity and merges on a key, so a rerun adds no duplicates. Use `dlt_destination()` for your own dlt pipelines.
- **See where a table comes from**: the catalog links a table built by dbt or loaded by Ingestion to its pipeline under **Produced by**. Two more agent skills, `dbt-platform` and `ingestion`, come with the installer.
- **Documentation site** at https://sanderdw.github.io/iceberg-data-platform/, from installation to extensions. The presentation is at [/presentation/](https://sanderdw.github.io/iceberg-data-platform/presentation/).
- Smaller images (the notebook image goes from 3.3 to 2.1 GB) and updated dependencies. Notebooks get Polars 2; see the [Polars 2 upgrade guide](https://docs.pola.rs/releases/upgrade/2/) for your own notebooks.

## 0.8.0

Not released; its changes are part of 0.9.0.

## 0.7.0 - 2026-10-04

- **Extensions.** Separately released extensions add capabilities through the Extension Bridge. Team administrators enable them per team and environment; they never receive secrets or storage keys. See [extensions](extensions.md).
- **Conversational BI**, the first extension: chat with your semantic models, including shared ones, and get charts with the SQL and metric definitions behind them. The installer can add it, with OpenAI, Anthropic, Google Gemini or Amazon Bedrock. See [installation](conversational-bi.md#install).
- **Any AI agent answers from your semantic models.** The MCP tool `query_semantic_model` builds the SQL from the model's agreed metrics and joins, and reads with your own permissions. See [MCP tools](mcp.md).
- **Build semantic models with your coding agent.** The `semantic-model` skill interviews you about the questions the model must answer, tests each one against the real data and publishes after your approval. See [agent skills](agent-skills.md).
- **Where a table comes from:** the catalog shows a table's description and, for tables a pipeline produces, the last run, test outcome and upstream tables.

## 0.6.0 - 2026-09-30

- **Polaris 1.8.0 adds semantic models to the Catalog.** Explore datasets, fields, metrics and relationships alongside your tables, and use the models with AI agents. `describe_semantic_model` can also return the stored definition.
- A semantic model's **Diagram** tab draws its datasets as tables and its relationships as lines between the join columns. Hover a dataset to follow its relationships, or select it to open the table.
- **Data shares can include semantic models.** Selecting a model also selects the tables it reads, and recipients read exactly the shared models, never list or change them. When a share leaves out a table that a shared model reads, the portal and the API name it. Add `includeModelTables=true` to the API call to add those tables instead.
- **AI agents can manage data shares.** New MCP tools list outgoing and received shares, and let team administrators create, edit and revoke shares and replace their secrets. Sharing a semantic model through MCP adds the tables it reads.
- The flight-data notebooks use semantic models stored in the platform: notebook 06 publishes the flights model, and notebook 07 reads it without local model files.
- Notebooks get 2 GB of memory by default (`NOTEBOOK_MEMORY`).
- Fixed: a second write to a table could fail when connecting with DuckDB from your own computer. Download the updated `iceberg_connect.py` helper from the portal.

## 0.5.3 - 2026-09-24

- **Participants get going from a single slip.** The `demo-company` skill now also writes printable sign-in slips, one per participant. Each slip holds the user portal address with a QR code, the username and the one-time password.
- **quick-share now uses two addresses instead of three, and your own tools work through them.**
  - The user portal address also carries the Iceberg catalog and S3 storage, so DuckDB, PyIceberg and DBeaver work on participants' laptops.
  - The administration address carries sign-in.
  - A gateway keeps the Polaris management API, the RustFS console and the Keycloak administration console off the internet.
- `uv run iceberg_connect.py shell db-…` opens the DuckDB CLI with the database attached, on macOS, Linux and Windows. The old `duckdb -init <(…)` only worked in bash.
- The helper checks the catalog before DuckDB starts. It names an address it can't reach and tells the user to download it again from the portal.
- Getting started shows install commands for macOS, Linux and Windows. It also warns when the catalog address only works on the platform's own machine.
- The administration portal points existing databases at the current `S3_ENDPOINT` when it starts. A database created before the platform got a public address now works from outside, so `S3_ENDPOINT` no longer has to be set before databases are created.
- Updated dependencies, including marimo 0.25.0 and uv 0.12.18. Notebooks still ignore a `.env` file in the team workspace, so one member's `.env` never sets variables for the whole team.

## 0.5.2 - 2026-09-24

- New agent skill `quick-share` opens the portals to participants anywhere for a class or demo. Free Cloudflare quick tunnels give public HTTPS addresses with trusted certificates, so nobody imports a certificate. It replaces the `lan-access` skill. See [agent skills](agent-skills.md).
- The installer ends with what to do next.
- Fixed: deleting a database could remove the storage bucket of another database when a catalog admin had changed the catalog's bucket property. The portal now only deletes a bucket that its tags assign to that database, and otherwise stops with nothing deleted.

## 0.5.1 - 2026-09-23

- **Connect from your machine:** use PyIceberg, DuckDB, the DuckDB CLI or DBeaver with your own account instead of a notebook. **Catalog › Connect from your computer** shows the snippets for each database; the `iceberg_connect.py` helper signs you in through the browser.
- **Getting started** pages in both portals show the three ways in: the portal, your own tools or scripts, and an AI agent through MCP.
- MCP works with Codex, GitHub Copilot, Cursor, Gemini CLI and other MCP clients, not only Claude Code.
- **Try it out** works for writes in the administration API documentation at `/docs`.
- The installation folder includes two agent skills: `lan-access` opens the portals to other devices on your network over HTTPS, and `demo-company` sets up a demo company with teams, databases and accounts for a class. See [agent skills](agent-skills.md).
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
