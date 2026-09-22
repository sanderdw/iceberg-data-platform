# Iceberg Workspaces

A standalone user portal with shared team [marimo](https://marimo.io/) notebooks. Users sign in with Keycloak, switch between their teams and environments and browse the databases, namespaces, tables and views they can access.

## Start

Start the [platform stack](../README.md) first, then build and start the workspace stack:

```bash
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Open http://localhost:3002. Sign in with the Keycloak account created or linked by your administrator. Port 3000 is the separate administration portal.

Signed-in users can open [API documentation](http://localhost:3002/docs) and use **Try it out**.
The API uses your portal session and enforces your existing team permissions. The selected
team and environment scope operations; use `PATCH /api/team` and `PATCH /api/environment`
to change them. The OpenAPI schema is available at `/openapi.json` after sign-in.
HTTP clients must send the session cookie; writes also require `X-Portal-Request: 1`
and `Content-Type: application/json` (send `{}` for operations without a body).
With Keycloak enabled, sign in through `/auth/login`; `POST /api/session` is only for
the local username/client-secret login mode. Swagger supplies the write headers automatically.

The Compose project is `iceberg-workspaces`; the data and administration project is `iceberg-platform`. The workspace gateway can stop and restart independently. It connects directly to Polaris and RustFS and does not require the administration portal API.

### Phone access

Set `PORTAL_LAN_IP` in `.env` to your host's LAN address:

```bash
docker compose -f compose.users.yaml -f compose.users.lan.yaml up -d --wait users
```

Open `http://<LAN-IP>:3002` on the same network. Return to localhost only with `docker compose -f compose.users.yaml up -d --wait users`.

## Appearance

The user portal follows the same Nothing-inspired design as the administration portal: monochrome surfaces, Doto headlines, Space Grotesk body text and Space Mono labels. Fonts are served locally. Use the theme button in the header to switch between dark and light modes; the browser remembers your choice. Operation messages and errors appear inline. The embedded marimo editor retains its own appearance settings.

## Work with your team data

Open **Team overview** to see the active team’s description, your role, members and their team roles, databases in the selected environment, and your active notebook count. Links lead to Catalog, Notebooks and Data shares. All team members can view this overview; the member list contains only the active team and refreshes with the workspace. Team membership and role changes remain in the administration portal.

Team Administrators and Database + bucket administrators can create, rename and delete databases in their active team and environment from **Team overview**. A new database gets its own Iceberg catalog and RustFS bucket; existing members receive access according to their team roles. Renaming changes only the display name: the catalog ID, bucket and connection settings remain stable. Names must be unique within a team and environment. Deleting a database immediately removes its tables, views, files, bucket and data shares, including in Production. Type its exact name to confirm. If cleanup fails, use **Resume deletion** in Team overview. Saved notebook files belong to the team and environment and remain available. Moving a database to another team remains a platform administrator action.

The top menu, selected database and namespace, and active team/environment are recorded in the URL. Reloading or using browser Back/Forward restores that location. Workspace lists, catalog listings and data shares refresh every 30 seconds while visible and when you return to the tab. Refresh preserves share forms, credential panels, catalog filters and open notebook frames. Table details and previews retain their explicit refresh controls.

Keycloak access renews automatically while the portal or a notebook is in use, for up to eight hours and subject to the Keycloak session limits. Saved team files survive session expiry. Existing native DuckDB attachments cache credentials; reconnect them to retrieve a fresh token without restarting marimo.

Select an **Active team** and **Environment**, choose a database and browse its namespaces. The team selector shows your role in each team; roles are assigned per team, so you can read in one team and write in another. Filter the current listing by name or object type. Database summaries show the team, environment and catalog connection information; namespace summaries include their properties.

Choose **Details →** on a table or view. Table tabs show the schema (including nested field IDs and descriptions), snapshot statistics, history, branches and tags, partition specifications, sort orders and properties. **Preview** reads up to 100 rows from the selected snapshot only when you choose **Load preview**. **Preview snapshot** in the history selects that snapshot for a historical read. Snapshot IDs and large integer values retain their full precision. Record totals are metadata statistics and can include rows affected by delete files.

View tabs show the output schema, current SQL definition and dialect, version history and properties. Views do not have table snapshot or preview controls: execution requires a compatible SQL engine. **Open in marimo** remains available for databases, namespaces and selected tables; views can be loaded with `catalog.load_view(...)` from a notebook. All catalog inspection controls are read-only; metadata edits, compaction, rollback and snapshot expiration are outside this first release.

Your user permissions apply to every catalog and data operation. Details use the signed-in user's Polaris token. Preview reads run DuckDB's Iceberg extension in a disposable process with that user's token and table credentials vended by Polaris; platform credentials and local catalog configuration are not inherited. Access is checked before the read and again before returning its result. Previews do not start or modify a notebook. A preview can never take the portal down for other users: a failing or crashing preview ends only its own process and is reported as unavailable. Previews are limited to two concurrent requests, 30 seconds of wall time, 20 seconds of CPU time, 512 MB of DuckDB memory and 4 GiB of virtual address space per process (DuckDB reserves far more address space than it uses), and under memory pressure the kernel ends the preview before the portal. DuckDB renders every value as text, so Iceberg v3 `variant` (as JSON), `geometry` (as WKT) and nanosecond timestamps preview like any other column. The extensions are installed when the image is built; a preview never downloads them. `uv.lock` pins the `duckdb` package but not its extensions, so both images record the builds they received: the image build log lists them and `cat /opt/duckdb/extensions/VERSIONS` in the user portal or notebook image shows them later. Display is limited to 50 top-level columns and 512 characters per cell. Large scans may hit those limits even when only 100 rows are requested; use marimo for those tables.

Each team and environment combination has one shared `/work` filespace containing multiple notebooks, across all databases in that environment. Environments are **Development**, **Acceptance** and **Production**. For example, `team-a` with `sander` and `alex`, and `db1` in Development and Production, has exactly two filespaces. Both members can open them concurrently and see each other’s saved files. Additional databases in Development reuse the Development filespace. Storage is allocated when a notebook first opens.

Execution runs separately for each signed-in session and database, using that user’s credentials. Switching team or environment, signing out or session expiry stops only that session’s execution; teammates keep working. Saved files survive stopping and reopening. Save changes before switching: unsaved edits and Python memory are lost when execution stops. Shared files are not a collaborative text editor: coordinate edits to the same file to avoid overwriting each other’s saves.

Run cells with **▶** in marimo. Add Python or SQL cells in the editor and use the provided `catalog`, `table` and `df` variables. Choose **Shared files** in the notebook selector to browse, create and open team notebooks, or select the starter notebook and examples directly.

Notebooks have outbound internet access for APIs, downloads and package installation. Use marimo's package installer with **uv** (the default for workspaces without a saved package manager preference), or run this in a Python cell:

```python
import subprocess
import sys

subprocess.run(
    ["uv", "pip", "install", "--python", sys.executable, "--target", "/tmp/packages", "humanize"],
    check=True,
)
```

Installed packages are available to notebook imports from `/tmp/packages`. They are private to the running container and disappear when it stops; reinstall them when reopening a workspace. Package downloads, caches and installs share the runtime's 256 MiB `/tmp` limit. For large packages or dependencies needed on every start, add them to the `notebook` uv dependency group and rebuild the notebook image.

The notebook image includes Altair for charts, Polars for dataframes and
`nbformat` for Jupyter export. Notebook execution and HTML/Jupyter exports work
without a bundled browser. PDF, thumbnail and screenshot exports are not included.
Browser testing remains a development tool.

## Connect an MCP client

The user portal serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp`, so an AI agent such as Claude Code can explore your team's data with your own Keycloak identity and Polaris permissions. Register the platform with a pre-registered public Keycloak client:

```bash
claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg http://localhost:3002/mcp
```

The first tool call opens Keycloak in your browser; sign in with your platform account. The callback returns to `http://localhost:3010/callback`, which is the only redirect Keycloak accepts for this client: Keycloak allows a wildcard at the end of a redirect path but not in its port, so `--callback-port` must equal `MCP_CALLBACK_PORT` in `.env`. To use another port, change that value, run `docker compose run --build --rm keycloak-bootstrap` and register the server again with the new port. Other MCP clients need the same three settings: the `/mcp` URL, the client ID `iceberg-mcp` and a callback on that port. Claude Code is the tested client; Claude Desktop custom connectors require a public HTTPS address and are out of scope for a localhost installation.

Catalog tools mirror the portal's read operations: `list_databases` (your teams, roles and databases), `list_namespaces`, `list_tables`, `describe_table`, `describe_view` and `preview_rows` (up to 100 rendered rows). They use your own Polaris grants, do not run SQL and cannot change catalog contents. `list_databases` includes databases whose deletion can be resumed. Other tools take its opaque database ID because display names repeat across environments.

If you are currently an Administrator or Database + bucket administrator of a team, the MCP server also offers `create_database`, `rename_database` and `delete_database` for that team's databases. Creation takes the team ID and environment; rename preserves the catalog ID and bucket. Deletion is marked destructive for agents, immediately removes data in every environment and requires `confirm_name` to match the current display name, including when resuming an interrupted deletion. The server rechecks your Keycloak identity link, team role and database ownership on each call. Access tokens last 15 minutes and are refreshed by the MCP client, which keeps an offline refresh token on your machine; it stays valid for 30 days of inactivity or until an administrator revokes it in Keycloak. Portal session cookies do not authorize `/mcp`, and MCP tokens do not open the portal. Platform administrators can register the administration portal's endpoint as a second server; see [the administration guide](../docs/admin-guide.md#connect-an-mcp-client).

## Share data with an external party

Use the top menu to switch between **Team overview**, **Catalog**, **Notebooks**, and **Data shares**. Switching sections keeps your running notebook open. Team and environment selectors apply to all four sections.

**Data shares** lists shares by database for the active team and environment. Readers and writers can view these shares; management buttons are greyed out with an explanation that team administrator privileges are required. With the Administrator or Database + bucket administration role, choose **New data share** under a database, name the share, choose **Another team**, **Externally**, or both, tick the tables and views, and optionally set an expiry. For team sharing, select a recipient team. Its current and future members receive read access through their existing accounts; removing a member removes this access. Received shares appear under **Shared with this team** and as read-only shared databases in **Catalog** and **Notebooks** in the matching environment. A recipient team does not need its own database to open a notebook. Catalog browsing shows only selected shared objects; notebooks use the recipient team’s filespace and the signed-in user’s permissions. Revoking or expiring the last share removes the database from that team’s workspace. Team sharing grants no write or bucket administration access. Sharing destinations are fixed at creation; revoke and recreate a share to change them. For external sharing, the client ID and client secret appear once. Use **Copy DuckDB snippet** to copy a runnable Python script; the code is not displayed in the panel. Send these to the recipient over a secure channel.

The recipient can read exactly what you selected and cannot list anything else, so the snippet names the shared objects in full. A view shares only its definition: add every table it reads, and remember the recipient can read those tables completely. **Edit** changes the selection or expiry, **New secret** replaces a lost secret and ends the old one, **Revoke** ends access immediately. Every administrator of the team manages the same shares. See [the resource model](../docs/CONTEXT.md#data-shares) for the details.

Save the copied script as `read_share_duckdb.py` and run `uv run read_share_duckdb.py`.
Comments list all shared tables and views; the example query prints only the first
shared table. Change that query to read another shared table. The script does not
execute views; see [client connection details](../docs/admin-guide.md#connect-an-iceberg-client).

## Bundled examples

1. **01 · Neighborhood data with PyIceberg** creates `synthetic.neighborhood_electricity` with 40 homes, seven days and 26,880 quarter-hour readings. The deterministic model includes consumption, solar generation, grid draw, grid export and fictional addresses. Run the cells from top to bottom. A populated table is skipped; existing rows are never overwritten or duplicated. Writing requires a writer role or higher in the active team.
2. **02 · Visualize with DuckDB** reads that Iceberg table using your credentials, then runs SQL on the loaded dataframe. Set `STREET` in the first cell to filter the hourly aggregation, statistics and chart. A second chart compares grid draw and export by street. Readers can use this notebook once a writer has created the table. Rerun the data-loading cell to fetch changes made by another notebook.
3. **03 · Native DuckDB on Iceberg** attaches Polaris directly with DuckDB's `iceberg` extension. Set `NAMESPACE` and `TABLE` in the settings cell, then run the notebook to inspect tables, columns, the first 100 rows, row counts, snapshots and energy totals using SQL cells. It defaults to the table selected in the portal or `synthetic.neighborhood_electricity`. The attachment is read-only and readers can use it.
4. **04 · Write Iceberg v3 with DuckDB** creates the Iceberg format-version 3 table `iceberg_v3.sensor_events` with DuckDB and writes 480 fictional sensor events. It uses what v3 adds: a `VARIANT` payload, a nanosecond `TIMESTAMP_NS`, a `GEOMETRY` location, a column added with a default value, and an update and delete recorded as binary deletion vectors. It has no controls: it runs from top to bottom and recreates its own table on every run, so the result is always the same. It marks that table with a table property and stops rather than replace a table of the same name that it did not create. Writing requires a writer role or higher in the active team.
5. **05 · Read Iceberg v3 with DuckDB** queries that table: fields inside the variant, nanosecond precision, geometry, the default value, row lineage (`_row_id`, `_last_updated_sequence_number`), the Puffin deletion vectors and time travel to the first snapshot. It runs from top to bottom without input and readers can use it once a writer has run example 4.
6. **06 · Write an AI-ready flights product** uses native DuckDB SQL to generate and publish 12,000 synthetic flight instances, 8 airports, 3 carriers, 120 aircraft, 56 routes and 16 runways in `ai_flights`. The unchanged Apache Ossie `flights.yaml` supplies all six dataset mappings; `flights.product.yaml` adds demo policies, metric SQL and 11 executable quality checks. Both YAML files are beside the notebooks. Timestamps represent UTC, distances are miles and delays are minutes. Canceled flights have missing actual observations. The default covers January 1–30, 2026. Writing requires a writer role. The notebook checks every target table's ownership before replacing its own tables; reruns do not append duplicates. Each table is a separate commit, so finish publication before reading and rerun after interruption.
7. **07 · Read an AI-ready flights product** reads those Iceberg tables directly using DuckDB and a read-only attachment. It verifies semantic-file hashes against table properties, reruns the quality checks, follows the ontology from flight routes to departure airports, charts delays, compares carrier punctuality and demonstrates denominator and join-cardinality mistakes. It needs no AI API or external download. The SQL generator implements this specific Ossie example; it is not an Ossie runtime or general ontology compiler. The extra product contract is local to this demonstration, not an Ossie specification extension.


The Iceberg v3 examples cover the v3 types and capabilities that DuckDB 1.5.5 writes natively. `GEOGRAPHY` and `UNKNOWN` are planned for DuckDB 2.0, and DuckDB has no nanosecond timestamp with time zone (`timestamptz_ns`), so the examples leave those out. The portal's table preview reads with DuckDB and shows these tables. The starter notebook reads the selected table with PyIceberg, which cannot read `variant` or `geometry` columns yet, and shows a notice for such tables; use example 5 or your own DuckDB cells there.

The native example follows [DuckDB's Iceberg REST catalog workflow](https://duckdb.org/docs/lts/core_extensions/iceberg/iceberg_rest_catalogs): in-memory secrets and `ATTACH ... (TYPE iceberg)`. Its connection helper obtains table-scoped storage credentials from Polaris using the signed-in user's identity and sets the internal RustFS endpoint. Automatic credential vending on the attachment is disabled to prevent Polaris's host-facing S3 address from replacing that internal endpoint. Reconnect to renew expiring credentials, see a fresh snapshot or query a different table. `connect_duckdb(..., read_only=False, missing_ok=True)` attaches writable for a table that does not exist yet; after `CREATE TABLE`, `refresh_table_credentials` vends storage credentials for the new table, so creating and inserting are separate statements. The read-only attachment is a convenience: Polaris enforces the user's role either way. HTTPX retrieves credentials and catalog configuration; DuckDB reads Iceberg metadata and Parquet without PyIceberg or a dataframe staging step. The notebook image bundles `httpfs` and `iceberg` for pinned DuckDB 1.5.5 under `/opt/duckdb/extensions`, so runtime internet access is unnecessary.

All notebooks run from top to bottom with plain code settings, static tables and charts. They contain no input forms, action buttons, dropdowns or interactive output widgets. Native SQL cells still feed the results and charts; rerun the appropriate cell to reload data or renew credentials. Write notebooks run their write steps when their cells execute and retain their existing ownership and rerun safeguards.

New work directories receive the examples and companion YAML/attribution files immediately. Existing directories receive missing files on their next start; existing notebooks and semantic files are never overwritten. After an image update, stop and reopen the workspace to receive new bundled files. For the flights pair, edit `NAMESPACE` in both notebooks to change the destination. The SQL metrics and checks in `flights.product.yaml` are executable local notebook code: review edits as you would SQL cells. Updating either YAML changes its hash; republish with example 6 before reading with example 7. Semantic files remain in the shared workspace and are not automatically distributed through Polaris.

```bash
uv run --all-groups marimo check user_portal/notebook/examples/*.py
# Native DuckDB execution against temporary data, including offline extensions, reader access, v3 and flights:
uv run --all-groups python -m scripts.duckdb_smoke
# With running stacks, Chromium and current images:
npm run test:examples
```

The browser test runs the examples, verifies that example 1 writes only after an explicit click and repeated writes do not duplicate data, checks SQL results and reactive charts, and runs the Iceberg v3 examples to completion. It cleans up its own data. Screenshots are written to `test-results/examples/`.

## Execution and access

- Python **3.14.7**, FastAPI **0.141.1** and marimo **0.24.2** are pinned in `uv.lock`.
- The gateway uses the platform identity to read the user, team and database directory and to manage data shares and databases for team administrators. It uses RustFS administration credentials for database creation and deletion. Catalog requests and notebook operations use the signed-in user's identity.
- Each editor has a private container and a separate Docker bridge network shared only with the gateway, Polaris and RustFS. The network allows outbound internet access. Runtime ports are not published. Native numerical thread pools are limited to two threads per library so multiple open notebook kernels fit the container’s 256-process/thread budget.
- Runtimes use UID 10001, writable `/work` and `/tmp`, no Linux capabilities, up to two CPUs, 256 processes and 1 GiB RAM by default. Notebook code receives only its user's credentials, with no platform secret, administrator cookies or Docker socket.
- The trusted gateway has Docker socket access to start and stop runtimes. It belongs to trusted platform administration. This shared Docker host is intended for local development and does not provide isolation for hostile tenants.
- HTTP and WebSocket proxy requests check session ownership and database access. Neither portal's cookies are forwarded to marimo. The internal runtime token is shared only between the gateway and notebook, never with the browser; it also authenticates requests for the owner's current access token.
- Sessions last at most eight hours, subject to Keycloak's session limits, and expire on restart or refused renewal. Authorization is checked on API and notebook requests and every 30 seconds for active sessions. User deletion, membership revocation and database moves close affected runtimes. Previously issued external data credentials remain subject to Polaris and RustFS expiry rules.
- Run one gateway replica because sessions and runtime coordination are process-local. The default limit is eight active notebooks.
- The MCP endpoint is a bearer-token API: it accepts only Keycloak tokens issued to the public `iceberg-mcp` client, validates their signature, issuer, audience and expiry, and maps them to the linked Polaris principal on every call. Catalog reads and previews use that user's own token, exactly like the portal. OAuth resource metadata is published at `/.well-known/oauth-protected-resource`.

## Configuration

The shared `.env` supplies `POLARIS_CLIENT_ID`, `POLARIS_CLIENT_SECRET`, `RUSTFS_ACCESS_KEY` and `RUSTFS_SECRET_KEY`. There is no second password file or user database. `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` are the addresses handed to data share recipients.

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_PORT` | `3002` | User portal host port |
| `PORTAL_LAN_IP` | Required for LAN | Extra binding with `compose.users.lan.yaml` |
| `CATALOG_DOCKER_NETWORK` | `iceberg-platform_default` | Existing platform network |
| `POLARIS_CONTAINER` | `iceberg-platform-polaris-1` | Polaris container for notebook networks |
| `RUSTFS_CONTAINER` | `iceberg-platform-rustfs-1` | RustFS container for notebook networks |
| `MAX_NOTEBOOKS` | `8` | Maximum simultaneous runtimes |
| `NOTEBOOK_MEMORY` | `1g` | Runtime memory limit |
| `USER_COOKIE_SECURE` | `false` | Set to `true` with HTTPS |
| `MCP_CALLBACK_PORT` | `3010` | Loopback port registered in Keycloak as the OAuth callback of MCP clients |
| `POLARIS_PUBLIC_URL` | `http://localhost:8181` | Catalog address given to data share recipients |
| `S3_ENDPOINT` | `http://localhost:9000` | Storage address shown to data share recipients; the address Polaris actually vends is fixed per database when it is created |
| `USER_S3_ENDPOINT` | `http://rustfs:9000` | Preview storage endpoint; set to the host-facing S3 URL when running the gateway outside Docker |

Default bindings use HTTP on localhost or LAN. Use HTTPS outside this local environment. A reverse proxy must support WebSockets and preserve the original Host header; set `FORWARDED_ALLOW_IPS` to its address so sign-in limits apply per visitor (see [SECURITY.md](../SECURITY.md)).

Shared team/environment volumes are named `iceberg-workspaces-work-<hash>` and labeled `iceberg.users.runtime=iceberg-workspaces`. Back them up before removing Docker data. `docker compose down` preserves these directories. Deleting a user or database closes affected runtimes but does not automatically erase shared notebook files.

## Tests

```bash
uv run --all-groups pytest
# With the demo fixtures from docs/keycloak.md, exercise the MCP endpoint end to end:
npm run test:mcp
# Browser fixtures need Chromium, but no running stack:
npm ci
npx playwright install chromium
npm run test:team-ui
npm run test:catalog
npm run test:shares-ui
npm run test:navigation-ui
# With Polaris and RustFS running, using temporary test data:
uv run --all-groups python -m scripts.catalog_smoke
uv run --all-groups python -m scripts.share_smoke
uv run --all-groups ruff check server user_portal test scripts/*.py
uv run --all-groups marimo check user_portal/notebook/template.py
# With both stacks running:
uv run --all-groups python -m scripts.users_smoke
# Including Chromium, live marimo WebSockets and table execution:
npm run test:users
```

Integration tests create temporary teams, databases, users, Parquet data and a view. They check authorization, runtime isolation, notebook reads, saved files, team switching and sign-out. They remove only their own resources and work volumes. Screenshots are written to `test-results/users-desktop.png` and `test-results/users-mobile.png`.


For native DuckDB connections, marimo lists catalog tables but only describes columns for tables whose scoped storage credentials are installed on that connection. This avoids requests to AWS S3 for unrelated RustFS tables during automatic dataset discovery. Use `refresh_table_credentials` with distinct secret names before querying additional tables. Rebuild the notebook image and stop/reopen an existing workspace to load helper changes.
