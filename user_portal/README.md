# Iceberg Workspaces

A standalone user portal with shared team [marimo](https://marimo.io/) notebooks. Users sign in with Keycloak, switch between their teams and environments and browse the databases, namespaces, tables and views they can access.

## Start

Start the [platform stack](../README.md) first, then build and start the workspace stack:

```bash
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Open http://localhost:3002. Sign in with the Keycloak account created or linked by your administrator. Port 3000 is the separate administration portal.

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

Select an **Active team** and **Environment**, choose a database and browse its namespaces. The team selector shows your role in each team; roles are assigned per team, so you can read in one team and write in another. Filter the current listing by name or object type. Database summaries show the team, environment and catalog connection information; namespace summaries include their properties.

Choose **Details →** on a table or view. Table tabs show the schema (including nested field IDs and descriptions), snapshot statistics, history, branches and tags, partition specifications, sort orders and properties. **Preview** reads up to 100 rows from the selected snapshot only when you choose **Load preview**. **Preview snapshot** in the history selects that snapshot for a historical read. Snapshot IDs and large integer values retain their full precision. Record totals are metadata statistics and can include rows affected by delete files.

View tabs show the output schema, current SQL definition and dialect, version history and properties. Views do not have table snapshot or preview controls: execution requires a compatible SQL engine. **Open in marimo** remains available for databases, namespaces and selected tables; views can be loaded with `catalog.load_view(...)` from a notebook. All catalog inspection controls are read-only; metadata edits, compaction, rollback and snapshot expiration are outside this first release.

Your user permissions apply to every catalog and data operation. Details use the signed-in user's Polaris token. Preview reads run DuckDB's Iceberg extension in a disposable process with that user's token and table credentials vended by Polaris; platform credentials and local catalog configuration are not inherited. Access is checked before the read and again before returning its result. Previews do not start or modify a notebook. A preview can never take the portal down for other users: a failing or crashing preview ends only its own process and is reported as unavailable. Previews are limited to two concurrent requests, 30 seconds of wall time, 20 seconds of CPU time, 512 MB of DuckDB memory and 4 GiB of virtual address space per process (DuckDB reserves far more address space than it uses), and under memory pressure the kernel ends the preview before the portal. DuckDB renders every value as text, so Iceberg v3 `variant` (as JSON), `geometry` (as WKT) and nanosecond timestamps preview like any other column. The extensions are installed when the image is built; a preview never downloads them. Display is limited to 50 top-level columns and 512 characters per cell. Large scans may hit those limits even when only 100 rows are requested; use marimo for those tables.

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

The base notebook image includes Altair for charts, Polars for dataframes, `nbformat` for Jupyter export, and `nbconvert[webpdf]` with Chromium and its system dependencies for PDF export.

## Bundled examples

1. **01 · Neighborhood data with PyIceberg** creates `synthetic.neighborhood_electricity` with 40 homes, seven days and 26,880 quarter-hour readings. The deterministic model includes consumption, solar generation, grid draw, grid export and fictional addresses. Run the cells and click **Create example table**. A populated table is skipped; existing rows are never overwritten or duplicated. Writing requires a writer role or higher in the active team.
2. **02 · Visualize with DuckDB** reads that Iceberg table using your credentials, then runs SQL on the loaded dataframe. The street selector reactively updates the hourly aggregation, statistics and chart. A second chart compares grid draw and export by street. Readers can use this notebook once a writer has created the table. **Reload Iceberg data** fetches changes made by another notebook.
3. **03 · Native DuckDB on Iceberg** attaches Polaris directly with DuckDB's `iceberg` extension. Choose a table and click **Connect / refresh credentials**, then inspect tables, columns, the first 100 rows, row counts, snapshots and energy totals using SQL cells. It defaults to the table selected in the portal or `synthetic.neighborhood_electricity`. The attachment is read-only and readers can use it.
4. **04 · Write Iceberg v3 with DuckDB** creates the Iceberg format-version 3 table `iceberg_v3.sensor_events` with DuckDB and writes 480 fictional sensor events. It uses what v3 adds: a `VARIANT` payload, a nanosecond `TIMESTAMP_NS`, a `GEOMETRY` location, a column added with a default value, and an update and delete recorded as binary deletion vectors. It has no controls: it runs from top to bottom and recreates its own table on every run, so the result is always the same. Writing requires a writer role or higher in the active team.
5. **05 · Read Iceberg v3 with DuckDB** queries that table: fields inside the variant, nanosecond precision, geometry, the default value, row lineage (`_row_id`, `_last_updated_sequence_number`), the Puffin deletion vectors and time travel to the first snapshot. It runs from top to bottom without input and readers can use it once a writer has run example 4.

Energy is measured in kWh per quarter-hour; hourly queries sum the intervals. Iceberg stores UTC timestamps, while charts display local time in Europe/Amsterdam. All addresses are fictional and the dataset is a demonstration, not a calibrated energy model. Example 2 reads up to 100,000 rows into a dataframe; add selective Iceberg filters for larger datasets. Example 3 runs scans and aggregations directly in DuckDB, returning only query results to marimo.

The Iceberg v3 examples cover the v3 types and capabilities that DuckDB 1.5.5 writes natively. `GEOGRAPHY` and `UNKNOWN` are planned for DuckDB 2.0, and DuckDB has no nanosecond timestamp with time zone (`timestamptz_ns`), so the examples leave those out. The portal's table preview reads with DuckDB and shows these tables. The starter notebook reads the selected table with PyIceberg, which cannot read `variant` or `geometry` columns yet, and shows a notice for such tables; use example 5 or your own DuckDB cells there.

The native example follows [DuckDB's Iceberg REST catalog workflow](https://duckdb.org/docs/lts/core_extensions/iceberg/iceberg_rest_catalogs): in-memory secrets and `ATTACH ... (TYPE iceberg)`. Its connection helper obtains table-scoped storage credentials from Polaris using the signed-in user's identity and sets the internal RustFS endpoint. Automatic credential vending on the attachment is disabled to prevent Polaris's host-facing S3 address from replacing that internal endpoint. Reconnect to renew expiring credentials, see a fresh snapshot or query a different table. `connect_duckdb(..., read_only=False, missing_ok=True)` attaches writable for a table that does not exist yet; after `CREATE TABLE`, `refresh_table_credentials` vends storage credentials for the new table, so creating and inserting are separate statements. The read-only attachment is a convenience: Polaris enforces the user's role either way. HTTPX retrieves credentials and catalog configuration; DuckDB reads Iceberg metadata and Parquet without PyIceberg or a dataframe staging step. The notebook image bundles `httpfs` and `iceberg` for pinned DuckDB 1.5.5 under `/opt/duckdb/extensions`, so runtime internet access is unnecessary.

Examples 1 to 3 use focused cells with explicit dependencies, local intermediate variables, reusable generation logic, `mo.stop` and a `run_button` for writes. Examples 4 and 5 are deliberately linear and have no controls; each write step passes a value to the next cell so marimo runs them in order. Widget changes flow through marimo's reactive graph without `on_change` callbacks or hidden state. Native SQL cells feed the charts. Filters select rows in the dataframe without interpolating user input into SQL. See the [marimo best practices](https://docs.marimo.io/guides/best_practices/) and [SQL guide](https://docs.marimo.io/guides/working_with_data/sql/).

New work directories receive the examples immediately. Existing directories receive missing files on their next start; existing notebooks are never overwritten. After an image update, stop and reopen the workspace to receive new bundled files.

```bash
uv run --all-groups marimo check user_portal/notebook/examples/*.py
# Native DuckDB execution against temporary data, including offline extensions, reader access and the Iceberg v3 examples:
uv run --all-groups python -m scripts.duckdb_smoke
# With running stacks, Chromium and current images:
npm run test:examples
```

The browser test runs the examples, verifies that example 1 writes only after an explicit click and repeated writes do not duplicate data, checks SQL results and reactive charts, and runs the Iceberg v3 examples to completion. It cleans up its own data. Screenshots are written to `test-results/examples/`.

## Execution and access

- Python **3.14.7**, FastAPI **0.141.1** and marimo **0.24.2** are pinned in `uv.lock`.
- The gateway uses the platform identity only to read the user, team and database directory. Catalog requests and notebook operations use the signed-in user's identity.
- Each editor has a private container and a separate Docker bridge network shared only with the gateway, Polaris and RustFS. The network allows outbound internet access. Runtime ports are not published.
- Runtimes use UID 10001, writable `/work` and `/tmp`, no Linux capabilities, up to two CPUs, 256 processes and 1 GiB RAM by default. Notebook code receives only its user's credentials, with no platform secret, administrator cookies or Docker socket.
- The trusted gateway has Docker socket access to start and stop runtimes. It belongs to trusted platform administration. This shared Docker host is intended for local development and does not provide isolation for hostile tenants.
- HTTP and WebSocket proxy requests check session ownership and database access. Neither portal's cookies are forwarded to marimo. The internal marimo token stays on the server.
- Sessions last eight hours and expire on restart. Authorization is checked on API and notebook requests and every 30 seconds for active sessions. User deletion, membership revocation and database moves close affected runtimes. Previously issued external data credentials remain subject to Polaris and RustFS expiry rules.
- Run one gateway replica because sessions and runtime coordination are process-local. The default limit is eight active notebooks.

## Configuration

The shared `.env` supplies `POLARIS_CLIENT_ID` and `POLARIS_CLIENT_SECRET`. There is no second password file or user database.

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
| `USER_S3_ENDPOINT` | `http://rustfs:9000` | Preview storage endpoint; set to the host-facing S3 URL when running the gateway outside Docker |

Default bindings use HTTP on localhost or LAN. Use HTTPS outside this local environment. A reverse proxy must support WebSockets and preserve the original Host header.

Shared team/environment volumes are named `iceberg-workspaces-work-<hash>` and labeled `iceberg.users.runtime=iceberg-workspaces`. Back them up before removing Docker data. `docker compose down` preserves these directories. Deleting a user or database closes affected runtimes but does not automatically erase shared notebook files.

## Tests

```bash
uv run --all-groups pytest
node scripts/catalog-browser.mjs
# With Polaris and RustFS running, using temporary test data:
uv run --all-groups python -m scripts.catalog_smoke
uv run --all-groups ruff check server user_portal test scripts/*.py
uv run --all-groups marimo check user_portal/notebook/template.py
# With both stacks running:
uv run --all-groups python -m scripts.users_smoke
# Including Chromium, live marimo WebSockets and table execution:
npm ci
npx playwright install chromium
npm run test:users
```

Integration tests create temporary teams, databases, users, Parquet data and a view. They check authorization, runtime isolation, notebook reads, saved files, team switching and sign-out. They remove only their own resources and work volumes. Screenshots are written to `test-results/users-desktop.png` and `test-results/users-mobile.png`.
