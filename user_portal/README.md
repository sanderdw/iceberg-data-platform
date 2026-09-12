# Iceberg Workspaces

A standalone user portal with personal [marimo](https://marimo.io/) notebooks. Users sign in with their existing username and client secret, switch between their teams and browse the databases, namespaces, tables and views they can access.

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

Select an **Active team**, choose a database and browse its namespaces. Open a database or table in marimo. Views can be loaded with `catalog.load_view(...)` from a notebook. Your user permissions apply to every catalog and data operation.

Each user, team and database combination has one personal `/work` directory containing multiple notebooks. Saved files survive stopping and reopening the workspace. Switching teams, signing out or session expiry stops execution. Save changes before switching: unsaved edits and Python memory are lost when execution stops. A second session can only open the same work directory after the first session closes it.

Run cells with **▶** in marimo. Add Python or SQL cells in the editor and use the provided `catalog`, `table` and `df` variables. The notebook selector above the editor switches between the starter notebook and examples.

## Bundled examples

1. **01 · Neighborhood data with PyIceberg** creates `synthetic.neighborhood_electricity` with 40 homes, seven days and 26,880 quarter-hour readings. The deterministic model includes consumption, solar generation, grid draw, grid export and fictional addresses. Run the cells and click **Create example table**. A populated table is skipped; existing rows are never overwritten or duplicated. Writing requires a writer role or higher.
2. **02 · Visualize with DuckDB** reads that Iceberg table using your credentials, then runs SQL on the loaded dataframe. The street selector reactively updates the hourly aggregation, statistics and chart. A second chart compares grid draw and export by street. Readers can use this notebook once a writer has created the table. **Reload Iceberg data** fetches changes made by another notebook.

Energy is measured in kWh per quarter-hour; hourly queries sum the intervals. Iceberg stores UTC timestamps, while charts display local time in Europe/Amsterdam. All addresses are fictional and the dataset is a demonstration, not a calibrated energy model. Visualization reads up to 100,000 rows; add selective Iceberg filters for larger datasets. DuckDB analyzes locally loaded data without network extensions.

The examples use focused cells with explicit dependencies, local intermediate variables, reusable generation logic, `mo.stop` and a `run_button` for writes. Widget changes flow through marimo's reactive graph without `on_change` callbacks or hidden state. Native SQL cells feed the charts. Filters select rows in the dataframe without interpolating user input into SQL. See the [marimo best practices](https://docs.marimo.io/guides/best_practices/) and [SQL guide](https://docs.marimo.io/guides/working_with_data/sql/).

New work directories receive the examples immediately. Existing directories receive missing files on their next start; existing notebooks are never overwritten. After an image update, stop and reopen the workspace to receive new bundled files.

```bash
uv run --all-groups marimo check user_portal/notebook/examples/*.py
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

Personal volumes are named `iceberg-workspaces-work-<hash>` and labeled `iceberg.users.runtime=iceberg-workspaces`. Back them up before removing Docker data. `docker compose down` preserves these directories. Deleting a user or database closes its runtime but does not automatically erase personal notebook files.

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
