# Iceberg Workspaces

A standalone user portal with shared team [marimo](https://marimo.io/) notebooks. Users sign in with their existing username and client secret, switch between their teams and environments and browse the databases, namespaces, tables and views they can access.

## Start

Start the [platform stack](../README.md) first, then build and start the workspace stack:

```bash
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Open http://localhost:3002. Use the username and client secret issued by the portal administrator. The OAuth client ID is used by Iceberg clients; the portal login asks for the username. Port 3000 is the separate administration portal.

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

Select an **Active team** and **Environment**, choose a database and browse its namespaces. Open a database or table in marimo. Views can be loaded with `catalog.load_view(...)` from a notebook. Your user permissions apply to every catalog and data operation.

Each team and environment combination has one shared `/work` filespace containing multiple notebooks, across all databases in that environment. Environments are **Development**, **Acceptance** and **Production**. For example, `team-a` with `sander` and `alex`, and `db1` in Development and Production, has exactly two filespaces. Both members can open them concurrently and see each other’s saved files. Additional databases in Development reuse the Development filespace. Storage is allocated when a notebook first opens.

Execution runs separately for each signed-in session and database, using that user’s credentials. Switching team or environment, signing out or session expiry stops only that session’s execution; teammates keep working. Saved files survive stopping and reopening. Save changes before switching: unsaved edits and Python memory are lost when execution stops. Shared files are not a collaborative text editor: coordinate edits to the same file to avoid overwriting each other’s saves.

Run cells with **▶** in marimo. Add Python or SQL cells in the editor and use the provided `catalog`, `table` and `df` variables. Choose **Shared files** in the notebook selector to browse, create and open team notebooks, or select the starter notebook and examples directly.

## Bundled examples

1. **01 · Neighborhood data with PyIceberg** creates `synthetic.neighborhood_electricity` with 40 homes, seven days and 26,880 quarter-hour readings. The deterministic model includes consumption, solar generation, grid draw, grid export and fictional addresses. Run the cells and click **Create example table**. A populated table is skipped; existing rows are never overwritten or duplicated. Writing requires a writer role or higher.
2. **02 · Visualize with DuckDB** reads that Iceberg table using your credentials, then runs SQL on the loaded dataframe. The street selector reactively updates the hourly aggregation, statistics and chart. A second chart compares grid draw and export by street. Readers can use this notebook once a writer has created the table. **Reload Iceberg data** fetches changes made by another notebook.
3. **03 · Native DuckDB on Iceberg** attaches Polaris directly with DuckDB's `iceberg` extension. Choose a table and click **Connect / refresh credentials**, then inspect tables, columns, the first 100 rows, row counts, snapshots and energy totals using SQL cells. It defaults to the table selected in the portal or `synthetic.neighborhood_electricity`. The attachment is read-only and readers can use it.

Energy is measured in kWh per quarter-hour; hourly queries sum the intervals. Iceberg stores UTC timestamps, while charts display local time in Europe/Amsterdam. All addresses are fictional and the dataset is a demonstration, not a calibrated energy model. Example 2 reads up to 100,000 rows into a dataframe; add selective Iceberg filters for larger datasets. Example 3 runs scans and aggregations directly in DuckDB, returning only query results to marimo.

The native example follows [DuckDB's Iceberg REST catalog workflow](https://duckdb.org/docs/lts/core_extensions/iceberg/iceberg_rest_catalogs): in-memory secrets and `ATTACH ... (TYPE iceberg)`. Its connection helper obtains table-scoped storage credentials from Polaris using the signed-in user's identity and sets the internal RustFS endpoint. Automatic credential vending on the attachment is disabled to prevent Polaris's host-facing S3 address from replacing that internal endpoint. Reconnect to renew expiring credentials, see a fresh snapshot or query a different table. HTTPX retrieves credentials and catalog configuration; DuckDB reads Iceberg metadata and Parquet without PyIceberg or a dataframe staging step. The notebook image bundles `httpfs` and `iceberg` for pinned DuckDB 1.5.5 under `/opt/duckdb/extensions`, so runtime internet access is unnecessary.

The examples use focused cells with explicit dependencies, local intermediate variables, reusable generation logic, `mo.stop` and a `run_button` for writes. Widget changes flow through marimo's reactive graph without `on_change` callbacks or hidden state. Native SQL cells feed the charts. Filters select rows in the dataframe without interpolating user input into SQL. See the [marimo best practices](https://docs.marimo.io/guides/best_practices/) and [SQL guide](https://docs.marimo.io/guides/working_with_data/sql/).

New work directories receive the examples immediately. Existing directories receive missing files on their next start; existing notebooks are never overwritten. After an image update, stop and reopen the workspace to receive new bundled files.

```bash
uv run --all-groups marimo check user_portal/notebook/examples/*.py
# Native DuckDB execution against temporary data, including offline extensions and reader access:
uv run --all-groups python -m scripts.duckdb_smoke
# With running stacks, Chromium and current images:
npm run test:examples
```

The browser test runs both examples, verifies that writing requires an explicit click and repeated writes do not duplicate data, then checks SQL results and reactive charts. It cleans up its own data. Screenshots are written to `test-results/examples/`.

## Execution and access

- Python **3.14.7**, FastAPI **0.141.1** and marimo **0.24.2** are pinned in `uv.lock`.
- The gateway uses the platform identity only to read the user, team and database directory. Catalog requests and notebook operations use the signed-in user's identity.
- Each editor has a private container and internal network shared only with the gateway, Polaris and RustFS. Runtime ports are not published. Notebooks have no internet connection. Add packages to the notebook image through the `notebook` uv dependency group.
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

Default bindings use HTTP on localhost or LAN. Use HTTPS outside this local environment. A reverse proxy must support WebSockets and preserve the original Host header.

Shared team/environment volumes are named `iceberg-workspaces-work-<hash>` and labeled `iceberg.users.runtime=iceberg-workspaces`. Back them up before removing Docker data. `docker compose down` preserves these directories. Deleting a user or database closes affected runtimes but does not automatically erase shared notebook files.

## Tests

```bash
uv run --all-groups pytest
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
