# Administration guide

For Keycloak account creation, linking, password resets and access revocation, use the
[identity management guide](keycloak.md). Human accounts use Keycloak. Polaris client credentials apply to service accounts.


Iceberg Platform runs the administration portal, Apache Polaris, PostgreSQL 18, pgAdmin and RustFS in the `iceberg-platform` Compose project. The separate [`iceberg-workspaces` stack](../user_portal/README.md) provides user sign-in and shared team marimo notebooks.

Python **3.14.7**, FastAPI **0.141.1** and uv **0.12.13** are pinned in the project manifests and Dockerfiles. Node is needed only for browser tests.

## Start the platform

Install Docker Compose and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
uv python install
uv sync --locked
uv run python -m scripts.setup
docker compose up -d --build
```

Open http://localhost:3000 and sign in through Keycloak with `PLATFORM_ADMIN_USERNAME` and the initial `PLATFORM_ADMIN_PASSWORD` from `.env`. Setup preserves existing configuration and never prints credentials.

| Service | Address | Authentication |
| --- | --- | --- |
| Administration portal | http://localhost:3000 | Keycloak platform administrator |
| FastAPI documentation | http://localhost:3000/docs | Sign in to the admin portal first |
| OpenAPI schema | http://localhost:3000/openapi.json | Admin portal session |
| RustFS console | http://localhost:9001 | Bucket administrator S3 credentials or local root credentials |
| Iceberg REST API | http://localhost:8181/api/catalog | OAuth2 client ID and secret |
| pgAdmin | http://localhost:5050 | `PGADMIN_EMAIL` + `PGADMIN_PASSWORD` |

Published ports bind to `127.0.0.1`. PostgreSQL is internal only. Set `PORT` to change the portal port. Swagger UI loads assets from jsDelivr; the OpenAPI schema remains available without the CDN.

For development with automatic reload:

```bash
docker compose up -d postgres rustfs polaris
uv run python -m server --reload
```

The Python entry point reads `.env`; existing environment variables take precedence. Use `PORT=3001 uv run python -m server` when the Docker portal already occupies port 3000.

### PostgreSQL and pgAdmin

PostgreSQL uses `postgres:18-alpine`. Its `postgres18-data` volume is mounted at `/var/lib/postgresql`, with data under `/var/lib/postgresql/18/docker`, following the [PostgreSQL 18 image layout](https://hub.docker.com/_/postgres). On first startup, Polaris bootstraps its schema and administrator credentials in the database.

Polaris 1.7.0 documents the [PostgreSQL JDBC metastore](https://polaris.apache.org/releases/1.7.0/metastores/relational-jdbc/), without an explicit PostgreSQL 18 compatibility matrix. This stack was verified with PostgreSQL 18.6: fresh Polaris bootstrap, `scripts.smoke` and `scripts.data_smoke` passed, covering catalog metadata, permissions and Iceberg reads/writes. Rerun these checks after changing either service version.

pgAdmin 9.17 runs at http://localhost:5050 and stores its settings in `pgadmin-data`. Setup generates a separate `PGADMIN_PASSWORD` in `.env`. Set `PGADMIN_EMAIL`, `PGADMIN_PASSWORD` and `PGADMIN_PORT` to customize the initial account and port. If omitted, the email defaults to `admin@example.com` and the password falls back to `PORTAL_PASSWORD`. The account settings apply when pgAdmin first initializes its volume; later password changes should be made in pgAdmin.

After signing in, expand **Iceberg Platform → Polaris metadata** and enter `POSTGRES_PASSWORD` from `.env`. The predefined connection uses host `postgres`, port `5432`, database `polaris` and username `polaris`. PostgreSQL remains accessible only on the Docker network. pgAdmin administers Polaris's metadata database; use the notebooks to query Iceberg table data.

### Access beyond localhost

Keycloak requires HTTPS and matching issuer/redirect URLs for remote portal access.
The optional LAN Compose files only change host bindings; configure the identity
origins and TLS before using them. See [Keycloak configuration](keycloak.md#configuration).

## Teams, databases and users

1. **Create teams** on the Teams page. Teams exist independently of databases and users. Editing a name or description preserves the stable team ID.
2. **Create a database** and select an existing team and Development, Acceptance or Production. Names are unique within that team and environment; `db1` can exist in both Development and Production. Each database gets its own catalog and RustFS bucket. Existing team members receive access immediately, with the role they hold in that team.
3. **Create a user**, select one or more teams and choose a role for each team. Every user must belong to at least one existing team. A role applies to all current and future databases of that team only, so one person can be Administrator in one team and Read in another.
4. **Copy the temporary password** shown once for a new Keycloak account. The user must change it at first sign-in. Existing linked accounts keep their own passwords.
5. **Edit memberships** under Users → Edit access. Check or clear teams; Polaris grants or removes access according to the selected teams.
6. **Change roles** in the same Edit access dialog, per team. Changing the role for one team leaves the user's access to other teams untouched. A data role does not grant portal administration.
7. **Move a database** under Databases → Move. Members of the new team receive access with their role in that team. Previous members lose access unless they also belong to the new team; a member of both teams switches to their role in the new team. The bucket, data, database name and connection details stay the same.
8. **Delete a database** under Databases → Delete. After confirmation, the operation removes catalog grants, namespaces, tables, views, objects, object versions, multipart uploads and the dedicated bucket. Users and teams remain. If cleanup fails, use **Resume deletion** to finish it.
9. **Delete a team** after moving or deleting its databases. The API refuses deletion if a user would lose their last team. Assign that user to another team or delete the user first. Users with multiple teams lose only the deleted membership.

Both portals use Keycloak. Human users are linked explicitly to Polaris principals; notebooks receive their Keycloak access token. Portal administrators require the Keycloak `platform-admin` client role. A database is an Iceberg catalog; engines such as Spark or Trino execute queries.

| Role | Permissions within the databases of the team it is assigned for |
| --- | --- |
| Read | Read namespaces, tables, views and data |
| Read & write | Manage data, tables, views and namespaces |
| Administrator | Manage contents, metadata and catalog access |

Human accounts access storage through credentials vended by Polaris. Direct S3 account
provisioning is not part of the Keycloak user workflow. A team without databases grants
no data access. See [identity management](keycloak.md#manage-users-in-the-administration-portal)
for linking, password reset, and access revocation.

## Administrator catalog browser

The **Catalog** page lets the signed-in portal administrator browse all Polaris databases, including catalogs created outside the portal. Expand databases and nested namespaces to see namespaces, tables and views. The browser supports search, refresh, empty states, retries and complete provider pagination. Contents load on expansion.

Both `/api/admin/explorer/` routes require the portal administrator session. A database user with an Administrator role does not receive portal administrator access. Polaris bearer tokens are not accepted as portal sessions. Signing out immediately revokes API access.

The browser receives only names, object kinds and namespace paths. It does not expose table data, view SQL, arbitrary metadata properties or credentials. While listing, the server temporarily grants itself listing privileges through a unique catalog role attached to `service_admin`, then removes that role even if the request fails. Existing team permissions and catalog roles are preserved. Failed cleanup produces an explicit error.

## Connect an Iceberg client

Managed notebooks automatically receive the user's Keycloak token:

```python
from user_portal.notebook.connection import connect
catalog = connect()
```

For external automation, a separately provisioned Polaris service identity can use
client credentials. Use its catalog ID as the warehouse. For example:

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog(
    "analytics",
    type="rest",
    uri="http://localhost:8181/api/catalog",
    warehouse="<catalog-id>",
    credential="<client-id>:<client-secret>",
    scope="PRINCIPAL_ROLE:ALL",
    **{
        "oauth2-server-uri": "http://localhost:8181/api/catalog/v1/oauth/tokens",
        "header.X-Iceberg-Access-Delegation": "vended-credentials",
    },
)
```

The service identity must have the required grants for each selected catalog. Human portal passwords are not Polaris client secrets.

## Persistence and operational behavior

See [architecture](architecture.md) for component boundaries. Teams are marked Polaris principal roles; users are principals with stable team IDs and a role per team. Each user has a dedicated principal role granting, per database, only the catalog role that matches the user's role in the owning team. Polaris stores this metadata in PostgreSQL; the portal has no separate database.

Only resources marked `portal.managed-by=iceberg-portal-v2` belong to this resource model. A fresh setup has no default teams or users. `docker compose down -v` deletes PostgreSQL, pgAdmin and RustFS data and resets the local environment.

Run one Uvicorn worker. A lock serializes administration changes, such as creating a user while deleting a team. Sessions are process-local and expire after eight hours or a restart. Multiple replicas require shared sessions and coordination.

Creation and moves use compensating actions when Polaris or RustFS fails. Failed compensation is reported explicitly. Database deletion is irreversible and stores a persistent deletion marker so cleanup can resume. Existing clients may retain temporary S3 credentials or cached metadata until their validity expires.

## API

The **Infrastructure** page displays live service health, probe response times and
recent successful-probe percentages, per-container CPU/memory/uptime/restarts,
PostgreSQL metadata statistics, managed bucket totals and RustFS filesystem space.
It refreshes every 15 seconds; bucket scans run every five minutes. Refresh fetches
the collector's latest snapshot rather than triggering an expensive scan.

Start or update it with `docker compose up -d --build --no-deps monitor portal`.
Compose connects the portal to the internal collector automatically. If running
the portal outside Compose, configure `MONITOR_URL` to reach that collector over
a private connection. The collector has no published host port by default.
Set `MONITOR_USERS_URL=http://users:3002` in `.env` and recreate `monitor` to include
health checks for the optional user portal. `MONITOR_PROJECTS` selects the Compose
projects whose container resources are collected.
Open notebooks carry the `iceberg-workspaces` project label, so each one appears as
a `notebook` container while its workspace is open.

Each source shows its own last successful update and marks stale or unavailable
readings. Missing metrics display a dash. Probe history holds up to 60 checks in
memory and resets on collector restart; it is not a persisted uptime SLA or a
measurement of actual API traffic. PostgreSQL transaction rates include monitoring
traffic, and deadlocks are cumulative since the PostgreSQL statistics reset.
The first transaction-rate sample is unavailable until a second reading exists.

Bucket totals cover current objects in portal-managed buckets, excluding previous
versions and incomplete uploads. Scans use a shared 45-second budget checked
between S3 requests and a 1,000-page limit per bucket. Incomplete scans remain
unavailable and never count as zero; concurrent writes can change counts during
a scan. Filesystem usage can include other data sharing RustFS's backing filesystem.

| Method | Path | Action |
| --- | --- | --- |
| GET / POST / DELETE | `/api/session` | Read session / sign in / sign out |
| GET | `/api/health` | Public process health |
| GET | `/api/admin/explorer/databases` | List all Polaris databases for the portal administrator |
| GET | `/api/admin/explorer/contents?database=name&namespace=part` | List namespaces, tables and views; repeat `namespace` for nested paths |
| GET | `/api/overview` | Provider status, teams, databases and users |
| GET | `/api/infrastructure` | Administrator-only cached monitoring snapshot |
| GET / POST | `/api/teams` | List / create teams |
| PATCH / DELETE | `/api/teams/{id}` | Edit / delete a team |
| GET / POST | `/api/databases` | List / create databases |
| PATCH / DELETE | `/api/databases/{id}` | Move with `{"team":"team-id"}` / delete including data |
| GET | `/api/databases/{id}/connection` | Iceberg connection details |
| GET / POST | `/api/users` | List / create users |
| PATCH / DELETE | `/api/users/{id}` | Replace memberships and per-team roles with `{"memberships":[{"team":"team-id","role":"writer"}]}`; returns the user and, on first S3 promotion, one-time `bucketCredentials` / revoke access |

Mutations require `Content-Type: application/json` and `X-Portal-Request: 1`, including DELETE without a body. Administration requires an authenticated portal session. Use stable team IDs from `/api/teams`. Validation errors return 422, missing teams 404 and dependency conflicts 409. Errors contain `error`; provider error bodies and secrets are never forwarded.

## Validation

```bash
npm run verify
uv run python -m scripts.smoke
uv run --all-groups python -m scripts.data_smoke
UV_NO_SYNC=1 npm run test:e2e
```

Integration and browser tests require running Polaris and RustFS services and installed Chromium. They cover team management, mandatory and multiple memberships, concurrent changes, compensation, resumable deletion, authentication, CSRF, real OAuth2 and S3 grants, Parquet data, nested namespaces, views and the administrator catalog browser. Full database deletion enables Polaris purge only for that catalog because view deletion requires it. Tests use unique resource names and clean up their own resources.
