# Architecture

```mermaid
flowchart LR
    Admin[Administrator browser] --> AdminAPI[FastAPI admin portal :3000]
    User[User browser] --> UserAPI[FastAPI user gateway :3002]
    AdminAPI --> Polaris[Apache Polaris]
    AdminAPI --> Storage[RustFS S3 and IAM]
    UserAPI --> Polaris
    UserAPI --> Docker[Trusted Docker daemon]
    Docker --> Notebook[Session marimo container]
    UserAPI -->|Authenticated HTTP and WebSocket proxy| Notebook
    Notebook -->|User credentials| Polaris
    Notebook -->|Vended credentials| Storage
    Polaris --> Postgres[(PostgreSQL metadata)]
```

## Services and ownership

`compose.yaml` runs the `iceberg-platform` project: PostgreSQL, RustFS, Polaris bootstrap, Polaris and the admin portal. `compose.users.yaml` runs the `iceberg-workspaces` gateway separately and provides the notebook image build. The gateway joins the existing catalog network; it does not depend on the administration portal's API.

Teams are marked Polaris principal-role records. Users are Polaris principals with stable team IDs, an individual principal role and the grants of their teams. Databases are Iceberg REST catalogs backed by dedicated RustFS buckets. There is no second application metadata database.

The admin identity manages metadata and grants. The user gateway uses that identity only to resolve the directory, then uses the user's OAuth token for catalog browsing. Notebook containers receive the user's own credentials. A user may belong to several teams; the active team and environment control the UI and workspace context, while the credentials retain the union of the user's team grants.

## Notebook lifecycle

A workspace has one shared volume per team/environment, keyed by the stable team ID and environment. Environments are Development, Acceptance and Production. All team members and databases in an environment share the same files. On first use the gateway creates the volume; on each runtime start, missing starter files are added without replacing existing files. Marimo serves the directory for browsing shared notebooks and examples.

Database display names are unique within a team and environment. Each database has an opaque, stable catalog ID used in REST paths and connection settings, so `db1` can exist in both Development and Production. Moves preserve the catalog ID and bucket, and reject name conflicts at the destination. Files stay with their team/environment when a database moves; no notebook migration is performed.

Each session/database execution receives its own container and internal Docker network containing the runtime, gateway, Polaris and RustFS. Runtime ports are not published. HTTP and WebSocket requests must belong to the authenticated session, active team and environment. Portal cookies and caller Authorization headers are stripped before forwarding to marimo.

Runtime containers run as UID 10001 with a read-only root filesystem, a writable work volume and temporary filesystem, dropped capabilities, no privilege escalation, and CPU/memory/process limits. They do not receive the Docker socket or platform-admin secrets. The gateway itself is trusted and has Docker-host administrative access.

Logout, team or environment switching, expiry and revoked authorization stop only the affected session’s runtimes. A gateway restart invalidates in-memory sessions and removes that stack's orphan runtimes and networks. Shared team/environment volumes persist. Multiple gateway replicas are not supported.

## Repository layout

| Path | Purpose |
| --- | --- |
| `server/` | Administration API, domain validation, Polaris and RustFS adapters |
| `public/` | Admin UI and canonical shared fonts/favicon |
| `user_portal/` | User API, authentication, notebook lifecycle and proxy |
| `user_portal/public/` | User interface; shares canonical assets with `public/` |
| `user_portal/notebook/` | Runtime image, connection helper and starter seeding |
| `user_portal/notebook/examples/` | Editable PyIceberg and DuckDB examples |
| `scripts/` | Credential setup, integration checks and source release tooling |
| `test/` | Unit, authorization and browser tests |
| `docs/` | Operating and release documentation |
| `.github/` | CI and contribution templates |

The supported public examples are the marimo notebooks. Historical Jupyter experiments, private credentials, presentation assets and generated evidence are not part of the source release.
