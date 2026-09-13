# Administration guide

Iceberg Platform runs the administration portal, Apache Polaris, PostgreSQL and RustFS in the `iceberg-platform` Compose project. The separate [`iceberg-workspaces` stack](../user_portal/README.md) provides user sign-in and shared team marimo notebooks.

Python **3.14.7**, FastAPI **0.141.1** and uv **0.12.13** are pinned in the project manifests and Dockerfiles. Node is needed only for browser tests.

## Start the platform

Install Docker Compose and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
uv python install
uv sync --locked
uv run python -m scripts.setup
docker compose up -d --build
```

Open http://localhost:3000 and sign in with `PORTAL_PASSWORD` from `.env`. Setup preserves existing configuration and never prints credentials.

| Service | Address | Authentication |
| --- | --- | --- |
| Administration portal | http://localhost:3000 | `PORTAL_PASSWORD` |
| FastAPI documentation | http://localhost:3000/docs | Sign in to the admin portal first |
| OpenAPI schema | http://localhost:3000/openapi.json | Admin portal session |
| RustFS console | http://localhost:9001 | Bucket administrator S3 credentials or local root credentials |
| Iceberg REST API | http://localhost:8181/api/catalog | OAuth2 client ID and secret |

Published ports bind to `127.0.0.1`. PostgreSQL is internal only. Set `PORT` to change the portal port. Swagger UI loads assets from jsDelivr; the OpenAPI schema remains available without the CDN.

For development with automatic reload:

```bash
docker compose up -d postgres rustfs polaris
uv run python -m server --reload
```

The Python entry point reads `.env`; existing environment variables take precedence. Use `PORT=3001 uv run python -m server` when the Docker portal already occupies port 3000.

### Phone access

Set `PORTAL_LAN_IP` in `.env` to the host's LAN address, then run:

```bash
docker compose -f compose.yaml -f compose.lan.yaml up -d --no-deps --wait portal
```

Open `http://<LAN-IP>:3000` on a phone connected to the same network. Only the portal gets the extra binding. Run `docker compose up -d --no-deps --wait portal` to return to localhost only.

## Teams, databases and users

1. **Create teams** on the Teams page. Teams exist independently of databases and users. Editing a name or description preserves the stable team ID.
2. **Create a database** and select an existing team and Development, Acceptance or Production. Names are unique within that team and environment; `db1` can exist in both Development and Production. Each database gets its own catalog and RustFS bucket. Existing team members receive access immediately.
3. **Create a user** and select one or more teams. Every user must belong to at least one existing team. The selected role applies to all current and future databases in those teams.
4. **Save credentials** when they appear. Secrets are returned once and are never stored or logged by the administration portal.
5. **Edit memberships** under Users → Edit teams. Existing credentials gain or lose catalog and S3 permissions according to the selected teams.
6. **Change roles** under Users → Edit role. The new role applies across all team databases without changing the username or database credentials. First-time bucket administrators receive new S3 credentials, shown once. Removing bucket administration denies all direct S3 access; restoring it reuses the same S3 credentials with the user’s current team buckets.
6. **Move a database** under Databases → Move. Members of the new team receive access. Previous members lose access unless they also belong to the new team. The bucket, data, database name and connection details stay the same.
7. **Delete a database** under Databases → Delete. After confirmation, the operation removes catalog grants, namespaces, tables, views, objects, object versions, multipart uploads and the dedicated bucket. Users and teams remain. If cleanup fails, use **Resume deletion** to finish it.
8. **Delete a team** after moving or deleting its databases. The API refuses deletion if a user would lose their last team. Assign that user to another team or delete the user first. Users with multiple teams lose only the deleted membership.

Users have OAuth2 client credentials and can also use their username and client secret to sign in to the user portal. The administration portal has a separate local administrator login. A database is an Iceberg catalog; engines such as Spark or Trino execute queries.

| Role | Permissions within the selected teams' databases |
| --- | --- |
| Read | Read namespaces, tables, views and data |
| Read & write | Manage data, tables, views and namespaces |
| Administrator | Manage contents, metadata and catalog access |
| Database + bucket administration | Catalog administration plus direct S3 access to team buckets |

Bucket administrators also receive an S3 access key and secret. Their policy grants `s3:*` only on their team buckets and the objects inside them. A team without databases grants no data access. Policies update when databases are created or moved and when memberships change. Other buckets and platform IAM administration are excluded. Direct S3 operations can modify Iceberg files; use Iceberg clients for normal table operations.

## Administrator catalog browser

The **Catalog** page lets the signed-in portal administrator browse all Polaris databases, including catalogs created outside the portal. Expand databases and nested namespaces to see namespaces, tables and views. The browser supports search, refresh, empty states, retries and complete provider pagination. Contents load on expansion.

Both `/api/admin/explorer/` routes require the portal administrator session. A database user with an Administrator or Database + bucket administration role does not receive portal administrator access. Polaris bearer tokens are not accepted as portal sessions. Signing out immediately revokes API access.

The browser receives only names, object kinds and namespace paths. It does not expose table data, view SQL, arbitrary metadata properties or credentials. While listing, the server temporarily grants itself listing privileges through a unique catalog role attached to `service_admin`, then removes that role even if the request fails. Existing team permissions and catalog roles are preserved. Failed cleanup produces an explicit error.

## Connect an Iceberg client

Open a database and copy its connection configuration. For example:

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog(
    "analytics",
    type="rest",
    uri="http://localhost:8181/api/catalog",
    warehouse="analytics",
    credential="<client-id>:<client-secret>",
    scope="PRINCIPAL_ROLE:ALL",
    **{
        "oauth2-server-uri": "http://localhost:8181/api/catalog/v1/oauth/tokens",
        "header.X-Iceberg-Access-Delegation": "vended-credentials",
    },
)
```

The same credentials work across the user's team databases; change the warehouse name to select another database.

## Persistence and operational behavior

See [architecture](architecture.md) for component boundaries. Teams are marked Polaris principal roles; users are principals with stable team IDs. Each user has a dedicated principal role granting only the catalog roles for their teams. Polaris stores this metadata in PostgreSQL; the portal has no separate database.

Only resources marked `portal.managed-by=iceberg-portal-v2` belong to this resource model. Older resources are not imported. The team/environment workspace model requires a fresh setup; it does not migrate user-specific notebook volumes or old database metadata. `docker compose down -v` deletes PostgreSQL and RustFS data and resets the local environment. A new environment has no default teams or users.

Run one Uvicorn worker. A lock serializes administration changes, such as creating a user while deleting a team. Sessions are process-local and expire after eight hours or a restart. Multiple replicas require shared sessions and coordination.

Creation and moves use compensating actions when Polaris or RustFS fails. Failed compensation is reported explicitly. Database deletion is irreversible and stores a persistent deletion marker so cleanup can resume. Existing clients may retain temporary S3 credentials or cached metadata until their validity expires.

## API

| Method | Path | Action |
| --- | --- | --- |
| GET / POST / DELETE | `/api/session` | Read session / sign in / sign out |
| GET | `/api/health` | Public process health |
| GET | `/api/admin/explorer/databases` | List all Polaris databases for the portal administrator |
| GET | `/api/admin/explorer/contents?database=name&namespace=part` | List namespaces, tables and views; repeat `namespace` for nested paths |
| GET | `/api/overview` | Provider status, teams, databases and users |
| GET / POST | `/api/teams` | List / create teams |
| PATCH / DELETE | `/api/teams/{id}` | Edit / delete a team |
| GET / POST | `/api/databases` | List / create databases |
| PATCH / DELETE | `/api/databases/{id}` | Move with `{"team":"team-id"}` / delete including data |
| GET | `/api/databases/{id}/connection` | Iceberg connection details |
| GET / POST | `/api/users` | List / create users |
| PATCH / DELETE | `/api/users/{id}` | Edit memberships with `{"teams":["team-id"]}` / revoke access |
| PATCH | `/api/users/{id}/role` | Change role with `{"role":"writer"}`; returns the user and, on first S3 promotion, one-time `bucketCredentials` |

Mutations require `Content-Type: application/json` and `X-Portal-Request: 1`, including DELETE without a body. Administration requires an authenticated portal session. Use stable team IDs from `/api/teams`. Validation errors return 422, missing teams 404 and dependency conflicts 409. Errors contain `error`; provider error bodies and secrets are never forwarded.

## Validation

```bash
npm run verify
uv run python -m scripts.smoke
uv run --all-groups python -m scripts.data_smoke
UV_NO_SYNC=1 npm run test:e2e
```

Integration and browser tests require running Polaris and RustFS services and installed Chromium. They cover team management, mandatory and multiple memberships, concurrent changes, compensation, resumable deletion, authentication, CSRF, real OAuth2 and S3 grants, Parquet data, nested namespaces, views and the administrator catalog browser. Full database deletion enables Polaris purge only for that catalog because view deletion requires it. Tests use unique resource names and clean up their own resources.
