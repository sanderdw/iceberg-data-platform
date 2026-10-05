# Architecture

```mermaid
flowchart LR
    Admin[Platform administrator<br/>browser or MCP client<br/>Keycloak role platform-admin]
    User[Platform user<br/>browser or MCP client<br/>Keycloak account linked to a user]

    subgraph AdminPortal[Admin portal · iceberg-platform]
        AdminAPI[FastAPI admin portal :3000<br/>UI and /mcp<br/>Keycloak client iceberg-admin]
    end
    subgraph UserPortal[User portal · iceberg-workspaces]
        UserAPI[FastAPI user portal :3002<br/>UI and /mcp<br/>Keycloak client iceberg-users]
        Docker[Trusted Docker daemon]
        Notebook[Session marimo container]
    end
    subgraph Extension[Extension, e.g. Conversational BI · own Compose project]
        ExtApp[Gateway: UI, API and /mcp<br/>Keycloak clients of the extension]
        Worker[Query worker]
    end
    subgraph Core[Shared core · iceberg-platform]
        Keycloak
        Bridge[Extension Bridge<br/>/bridge/v1 :3005]
        Polaris[Apache Polaris]
        Storage[RustFS S3 and IAM]
        Postgres[(PostgreSQL metadata)]
    end
    subgraph ThirdParty[Third party: external data share]
        Recipient[DuckDB or other Iceberg client]
    end

    Admin --> AdminAPI
    User --> UserAPI
    User --> ExtApp
    UserAPI -.->|Link| ExtApp

    AdminAPI & UserAPI & ExtApp -->|Sign-in| Keycloak
    AdminAPI --> Polaris
    AdminAPI --> Storage
    UserAPI --> Polaris
    UserAPI --> Docker --> Notebook
    UserAPI -->|HTTP and WebSocket proxy| Notebook
    Notebook -->|User credentials| Polaris
    Notebook -->|Vended credentials| Storage
    ExtApp -->|User context, automation principals| Bridge
    Bridge --> Polaris
    Worker -->|Short-lived Iceberg REST token| Polaris
    Worker -->|Vended credentials| Storage
    Polaris --> Postgres

    User -.->|Client ID, secret and DuckDB script| Recipient
    Recipient -.->|Read-only client credentials| Polaris
    Recipient -.->|Vended read-only credentials| Storage

    style AdminPortal fill:#3b82f61a,stroke:#3b82f6,stroke-width:2px
    style UserPortal fill:#10b9811a,stroke:#10b981,stroke-width:2px
    style Extension fill:#8b5cf61a,stroke:#8b5cf6,stroke-width:2px
    style Core fill:#64748b1a,stroke:#64748b,stroke-width:1px
    style ThirdParty fill:#f59e0b1a,stroke:#f59e0b,stroke-width:2px,stroke-dasharray:6 4
```

The two portals are separate applications with separate sign-ins. The **admin portal** accepts only Keycloak accounts with the `platform-admin` role and manages teams, users, databases and storage. The **user portal** accepts only Keycloak accounts linked to a platform user, and serves catalog browsing, shares and notebooks. Signing in with an account meant for the other portal fails: `platform-admin` is not a platform user, and a platform user is not an administrator.

**Extensions** are a third kind of application, for platform users. Each one runs in its own Compose project and signs in through its own Keycloak clients, whose tokens Polaris refuses. It reaches the core only through the **Extension Bridge**, which runs in the platform project next to the admin portal. The user portal links to each registered extension. See [extensions](extensions.md).

The third party never signs in to a portal. A team administrator hands over the share credential, and the recipient reads only the shared tables, views and semantic models through Polaris and RustFS.

## Services

| File | Project | Services |
| --- | --- | --- |
| `compose.yaml` | `iceberg-platform` | Admin portal, Extension Bridge, Keycloak, Polaris, PostgreSQL, RustFS, pgAdmin, monitoring |
| `compose.users.yaml` | `iceberg-workspaces` | User portal, plus the notebook image build |
| `extensions/conversationalbi/compose.yaml` | `iceberg-conversationalbi` | Optional Conversational BI extension, released separately (see [extensions](extensions.md)) |

- **Keycloak** signs in people and MCP clients. **Polaris** holds all application metadata (teams, users, databases, shares) and enforces data permissions; see the [resource model](CONTEXT.md). There is no separate application database. Polaris also stores the Apache Ossie semantic models of each namespace (beta in Polaris 1.8, see [semantic models](../user_portal/README.md#semantic-models)).
- **The user portal** joins the platform network and doesn't depend on the admin portal. Catalog browsing and notebooks use the signed-in user's own token. The portal uses its platform identity only for directory lookups and for the database and share actions of team administrators.
- **Both portals** keep sessions in memory ([deployment boundaries](keycloak.md#deployment-boundaries)) and serve `/mcp` for MCP clients, which authenticate every request with their own bearer token.

## Extensions

Extensions add functionality in their own Compose project and release cycle, and reach the core
only through the Extension Bridge (`bridge` service, `/bridge/v1`), Iceberg REST and S3. See
[extensions](extensions.md).

## Notebooks

- Each team and environment shares one filespace volume, across all its databases.
- Each session and database runs in its own container on a private network with the portal, Polaris and RustFS. The container runs as a non-root user with a read-only root filesystem and resource limits, and it never receives the Docker socket or platform secrets.
- Logging out, switching team or environment, or losing access stops that session's containers. Saved files persist.

The user portal controls Docker and is therefore a trusted service. The same goes for the internal `monitor` collector, which feeds the admin portal's Infrastructure page. See the [security model](../SECURITY.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `server/` | Administration API, OIDC, Polaris and RustFS adapters |
| `public/` | Admin UI and shared fonts/favicon |
| `user_portal/` | User API, notebook lifecycle and proxy |
| `user_portal/public/` | User interface |
| `user_portal/notebook/` | Notebook image, connection helpers and example notebooks |
| `scripts/` | Setup, integration checks and release tooling |
| `pgadmin/` | Preconfigured pgAdmin connection |
| `test/` | Unit and authorization tests |
| `docs/` | Documentation |
| `contracts/bridge/` | Extension Bridge contract: OpenAPI, table-property, network and handshake conventions |
| `extensions/` | Extensions with their own versions and releases, such as `extensions/conversationalbi` |
| `presentation/` | reveal.js deck, published to GitHub Pages and excluded from source releases |
| `screenshots/` | Design experiment captures, kept for reference and excluded from source releases |
