# Iceberg Data Platform

A local platform for managing team access to Apache Iceberg and exploring data in shared team marimo notebooks. It combines admin and user portals with Keycloak, Apache Polaris, PostgreSQL and RustFS.

![Iceberg Data Platform](docs/portal.png)

## What it does

- **Administration portal:** manage teams, users with a role per team, databases and data shares, and browse every Polaris catalog.
- **User portal:** sign in with Keycloak, pick a team and environment, and manage databases, browse catalogs, open notebooks and share data.
- **Catalog details:** table schemas, snapshots, branches and tags, partitioning, view SQL and a 100-row preview, all with your own permissions.
- **Data shares:** share selected tables and views with another team or with an external party, who gets a credential and a DuckDB script.
- **Team notebooks:** one shared marimo filespace per team and environment. Each user's execution is isolated and uses that user's own permissions. Seven example notebooks cover PyIceberg, DuckDB, Iceberg v3 and a semantic data product.
- **MCP servers:** AI agents such as Claude Code sign in through Keycloak to explore data or administer the platform.

This is a **local development platform**. Read [the security model](SECURITY.md) before deploying it elsewhere.

## Quick start

Install Docker with Compose v2 (Docker Desktop on macOS/Windows), then run:

```bash
# Linux / macOS
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh
```

```powershell
# Windows PowerShell
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

The installer creates `~/iceberg-data-platform`, generates `.env`, pulls the images and starts the platform. See the [installation guide](docs/install.md) for pinned versions, branch previews, updates and stopping.

### Run from source

Requires Docker with Compose v2, [uv](https://docs.astral.sh/uv/getting-started/installation/) and at least 4 GiB of Docker memory.

```bash
uv python install
uv sync --locked --all-groups
uv run python -m scripts.setup      # generates .env with random credentials; never commit it
docker compose up -d --build --wait
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Rerun the three Docker commands after source changes. Save your notebook work first: recreating the user portal stops running notebooks.

### Services

| Service                  | URL                               | Login                                                   |
| ------------------------ | --------------------------------- | ------------------------------------------------------- |
| Administration portal    | http://localhost:3000             | `platform-admin` + initial `PLATFORM_ADMIN_PASSWORD`     |
| User portal              | http://localhost:3002             | Keycloak account created or linked by an administrator |
| API documentation        | `/docs` on either portal          | Portal session                                          |
| Keycloak console         | http://localhost:8080/admin       | `admin` + `KEYCLOAK_ADMIN_PASSWORD`                      |
| Polaris Iceberg REST API | http://localhost:8181/api/catalog | Keycloak bearer token or client credentials             |
| RustFS console           | http://localhost:9001             | Bucket-admin or local root credentials                  |
| pgAdmin                  | http://localhost:5050             | `PGADMIN_EMAIL` + `PGADMIN_PASSWORD`                     |

Credentials are in `.env`. All ports bind to `127.0.0.1`, and PostgreSQL is internal only. For access beyond localhost, see [Keycloak configuration](docs/keycloak.md#configuration).

### First steps

1. Open the administration portal, sign in as `platform-admin` and change the temporary password.
2. Create a team, a database for that team, and a user in that team with the **Read & write** role.
3. Open the user portal, sign in as that user with the one-time password, and change it.
4. Open **Notebooks** and pick an example from the notebook selector. Start with **01**, which creates the data that the later examples read.

## Documentation

- [Installation](docs/install.md)
- [User portal and notebooks](user_portal/README.md)
- [Administration guide](docs/admin-guide.md)
- [Keycloak identity and access](docs/keycloak.md)
- [Resource model: users, teams, roles, databases, shares](docs/CONTEXT.md)
- [Architecture](docs/architecture.md)
- [Security model](SECURITY.md)
- [Contributing and tests](CONTRIBUTING.md)
- [Publishing a release](docs/releasing.md)
- [Changelog](CHANGELOG.md)
- [Presentation](https://sanderdw.github.io/iceberg-data-platform/)

## License

Application source is licensed under [Apache-2.0](LICENSE). Bundled fonts retain their [third-party licenses](THIRD_PARTY_NOTICES.md). This is an independent project, not an Apache Software Foundation project.
