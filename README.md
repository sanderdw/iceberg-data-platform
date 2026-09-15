# Iceberg Data Platform

A local platform for managing team access to Apache Iceberg and exploring data in shared team marimo notebooks. It combines two FastAPI portals with Apache Polaris, PostgreSQL and RustFS.

![Iceberg Data Platform](docs/portal.png)


**Independent project · Apache-2.0 · Python 3.14 · uv · English interface and documentation**

## What it does

- **Administration portal:** create and manage teams, assign users to one or more teams, create databases, move databases between teams, and delete databases with their stored data.
- **Administrator catalog explorer:** browse all Polaris catalogs, nested namespaces, tables and views. Database-admin roles do not grant portal-admin access.
- **Separate user portal:** log in with an existing username and client secret, switch teams and browse the active team's databases.
- **User catalog details:** inspect table schemas, snapshots, branches/tags, partitioning, sort orders and view SQL; preview up to 100 rows at a selected snapshot with your own data permissions.
- **Shared team workspaces:** one shared filespace per team and environment, with isolated execution using each user's data permissions.
- **Three included examples:** create 26,880 synthetic energy measurements with PyIceberg, visualize them with DuckDB, or attach Polaris directly for native DuckDB queries on Iceberg.

The Compose projects are `iceberg-platform` (administration portal, Polaris, PostgreSQL 18, pgAdmin and RustFS) and `iceberg-workspaces` (user portal and shared team marimo notebooks). They share the data services, not an application database. This is a **local development platform**, with an in-memory session model and a trusted Docker gateway. Read [the security model](SECURITY.md) before deploying elsewhere.

## Quick start

Requirements: Docker Engine or Docker Desktop with Compose v2, [uv](https://docs.astral.sh/uv/getting-started/installation/), and enough Docker memory for the data stack plus notebooks (budget at least 4 GiB for a small demonstration). Node.js 24+ is only needed for development and browser tests.

From a checkout or extracted source release:

```bash
uv python install
uv sync --locked --all-groups
uv run python -m scripts.setup

docker compose up -d --build --wait

docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Setup generates random development credentials in `.env`, sets restrictive file permissions, and preserves an existing `.env`. Do not commit or share this file.


| Service                  | URL                               | Login                                                     |
| ------------------------ | --------------------------------- | --------------------------------------------------------- |
| Administration portal    | http://localhost:3000             | `PORTAL_PASSWORD` from `.env`                             |
| User portal              | http://localhost:3002             | Existing username + issued client secret                  |
| Polaris Iceberg REST API | http://localhost:8181/api/catalog | OAuth client ID + secret                                  |
| RustFS console           | http://localhost:9001             | Issued bucket-admin credentials or local root credentials |
| pgAdmin                  | http://localhost:5050             | `PGADMIN_EMAIL` + `PGADMIN_PASSWORD` from `.env`            |

All default host ports bind to `127.0.0.1`. PostgreSQL is internal only. The portal's authenticated API documentation is available at `/docs` on port 3000.

pgAdmin includes a **Polaris metadata** server connection. Enter `POSTGRES_PASSWORD` from `.env` when connecting to the database. PostgreSQL 18 stores its data in the persistent `postgres18-data` volume. See the [administration guide](docs/admin-guide.md#postgresql-and-pgadmin) for configuration details.

### First session

1. Open the administration portal and log in.
2. Create a team, create a database under that team, then create a user with membership of that team. Choose **Read & write** (writer) to run both examples.
3. Save the one-time client secret. Open the user portal on port 3002 and log in using the username and that secret.
4. Select a team and database. Choose **01 · Neighborhood data with PyIceberg**, run the cells with ▶ and click **Create example table**.
5. Use the notebook selector to open **02 · Visualize with DuckDB**. Run its cells and change the street filter to explore energy consumption and solar production.
6. Open **03 · Native DuckDB on Iceberg** and click **Connect / refresh credentials** to query the Iceberg table directly, inspect snapshots and run SQL aggregations.

The first example skips a table that already contains rows. Readers can use the second example once a writer has created the dataset. Saved notebooks are shared per team and environment (Development, Acceptance or Production), across databases. Switching context or logging out stops only your execution; save your work first. Adding starter examples never overwrites existing team files.

### Optional LAN access

Set `PORTAL_LAN_IP` in your private `.env` to the host address reachable by your phone or other computer, then run:

```bash
docker compose -f compose.yaml -f compose.lan.yaml up -d --no-deps --wait portal
docker compose -f compose.users.yaml -f compose.users.lan.yaml up -d --wait users
```

Open that host address on port 3000 or 3002. LAN overrides require an explicit address and retain localhost access. Use HTTPS and the secure-cookie setting for deployments beyond trusted local development.

## Development and tests

The toolchain uses Python **3.14.7**, FastAPI **0.141.1**, marimo **0.24.2** and uv **0.12.13**. Python dependencies are locked in `uv.lock`; browser tooling is locked in `package-lock.json`.

```bash
uv sync --locked --all-groups
npm ci
npm run verify
npm run check:release
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
```

For live reload, start the data services and run the administration backend locally:

```bash
docker compose up -d postgres rustfs polaris
uv run python -m server --reload
```

With both stacks running:

```bash
npx playwright install chromium
npm run test:e2e
npm run test:stack
npm run test:data
npm run test:examples
```

Tests cover authorization, team membership, compensating actions, resumable deletion, real Iceberg reads/writes, notebook isolation, persistence, SQL results and browser interaction. Live tests create unique resources and clean up only their own data. The Docker SDK integration follows the active Docker CLI context, including Docker Desktop.

## Documentation

- [Users, teams, memberships, roles and database definitions](docs/CONTEXT.md)
- [Architecture and repository layout](docs/architecture.md)
- [Administration guide](docs/admin-guide.md)
- [User portal and notebooks](user_portal/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security and deployment boundaries](SECURITY.md)
- [Preparing a source release](docs/releasing.md)
- [Changelog](CHANGELOG.md)

## License

Application source is licensed under [Apache-2.0](LICENSE). Bundled fonts retain their [third-party licenses](THIRD_PARTY_NOTICES.md). This is an independent project and is not an Apache Software Foundation project.
