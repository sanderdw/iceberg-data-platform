# dbt internals

How the [dbt pipelines](../dbt.md) extension is built. Its source is in [`extensions/dbt`](https://github.com/sanderdw/iceberg-data-platform/tree/main/extensions/dbt): its own Compose project (`iceberg-dbt`), dependencies, images, version and releases. It uses the platform only through the [Extension Bridge](extension-bridge.md) v1, Iceberg REST and S3, so the platform and this extension upgrade independently.

| | |
| --- | --- |
| Engine | dbt v2 OSS 2.0.5 (Apache-2.0) with its built-in DuckDB adapter, DuckDB 1.5.5, Iceberg REST catalogs |
| API | [OpenAPI contract](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/dbt/contracts/dbt-api/v1/openapi.yaml) |
| Agent skill | [`skills/dbt-platform/SKILL.md`](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/dbt/skills/dbt-platform/SKILL.md) |
| Decisions | [`DECISIONS.md`](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/dbt/DECISIONS.md), including why `main` is the default for terminal commands |

## Architecture

The extension reaches the platform core only through the Extension Bridge (highlighted), Iceberg REST and S3.

```mermaid
flowchart LR
    subgraph Clients["Clients"]
        Agent["AI agent (MCP)"]
        Browser["Person (viewer)"]
    end

    subgraph Core["Iceberg Data Platform core"]
      KC["Keycloak<br/>iceberg-mcp / ext-dbt-mcp clients"]
        Polaris["Polaris<br/>Iceberg REST catalog"]
        S3["RustFS<br/>S3 storage"]
      Portal["User portal MCP<br/>localhost:3002/mcp<br/>catalog and table tools"]
    end

    subgraph Bridge["Platform Extension Bridge v1 (/bridge/v1)"]
      Disc["GET / : discovery<br/>contractVersion"]
      Me["GET /me<br/>teams, roles, databases"]
      AP["/teams/{team}/automation-principals<br/>list / enable / revoke"]
      Scope["GET /automation-principals/{id}<br/>scope"]
      Tok["POST /automation-principals/{id}/tokens<br/>short-lived Polaris token (read | write)"]
    end

    subgraph Dbt["dbt extension (Compose project iceberg-dbt)"]
      DP["dbt-portal MCP<br/>localhost:3004/mcp<br/>pipeline tools + viewer"]
        Git[("Git repos + run store")]
        L["dbt-launcher<br/>only Docker socket"]
        R["runner container<br/>dbt v2 + DuckDB<br/>no internet, read-only"]
    end

    Agent -- "MCP connection" --> Portal
    Agent -- "MCP connection" --> DP
    Browser -- "session" --> DP
    Portal -. "links to" .-> DP
    Agent -. "PKCE sign-in" .-> KC

    DP -- "client_credentials<br/>(service token)" --> KC
    DP == "user token forwarded" ==> Me
    DP == "user token forwarded" ==> AP
    DP == "contract check" ==> Disc
    DP == "service token" ==> Scope
    DP == "service token" ==> Tok
    DP --- Git

    DP -- "archive + run token" --> L
    L -- "starts, private 'launch' network" --> R
    R -- "Iceberg REST<br/>+ table properties publish" --> Polaris
    R -- "vended, table-scoped creds" --> S3
    Polaris -. "vends credentials" .-> S3

    classDef bridge fill:#ffcc00,stroke:#b38f00,stroke-width:3px,color:#000
    classDef core fill:#e8f0fe,stroke:#4a6fa5,color:#000
    classDef dbt fill:#e9f7ef,stroke:#2e7d4f,color:#000
    class Disc,Me,AP,Scope,Tok bridge
    class KC,Polaris,S3,Portal core
    class DP,Git,L,R dbt
    style Bridge fill:#fff8d6,stroke:#b38f00,stroke-width:3px
```

A build, step by step:

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (MCP)
    participant DP as dbt-portal
    participant KC as Keycloak
    participant B as Extension Bridge v1
    participant L as dbt-launcher
    participant R as Runner
    participant P as Polaris + S3

    A->>DP: build(project, environment) + user token
    DP->>B: GET /me (user token)
    B-->>DP: teams, roles, databases
    Note over DP: Services checks the role (writer/admin)
    DP->>KC: client_credentials
    KC-->>DP: service token
    rect rgb(255, 240, 180)
    Note over DP,B: Bridge: the extension's only way into the platform
    DP->>B: GET /bridge/v1 (discovery, contract range check)
    DP->>B: GET /automation-principals (find the team's active principal)
    DP->>B: GET /automation-principals/{id} (scope: catalogs)
    DP->>B: POST /automation-principals/{id}/tokens {access: write}
    B-->>DP: Polaris token (POLARIS_TOKEN_LIFETIME)
    end
    DP->>L: POST /runs (staged archive + token)
    L->>R: start isolated container (DBT_ENV_SECRET_POLARIS_TOKEN)
    R->>P: Iceberg REST commits + vended S3 writes
    R->>P: stamp producer, lineage, quality and docs properties
    R-->>L: exit code + artifacts
    DP->>L: poll /runs/{id}
    DP-->>A: run status, nodes, tests
```

## Runs

- **Credentials.** Each run receives a Polaris token from the Bridge, `write` for `build`/`run`/`seed` and `read` for everything else. Storage uses Polaris-vended, table-scoped credentials. The extension never holds a platform secret beyond its own Keycloak client.
- **Isolation.** A run is one container from the pinned runner image, started by the launcher (the only container with the Docker socket). The container has a read-only root, no capabilities and no internet. It sits on a private network with only the catalog and storage. Two builds of the same project and environment never overlap.
- **Capacity.** At most `MAX_CONCURRENT_RUNS` runs (default 4) run at once across all teams, and at most `MAX_RUNS_PER_TEAM` (default 2) of one team, so one team cannot take every slot. A waiting build holds no slot. Each run gets `RUN_MEMORY` (2g) and `RUN_CPUS` (2). Logs and files are kept for the newest `RUN_RETENTION` (30) finished runs per project and environment, plus the run the pipeline is drawn from and the newest docs; older runs keep their status and results.
- **Pipelines** are drawn from dbt v2's Parquet information schema (`--generate-info-schema`).

## Install from source

From the platform directory, with the platform running:

```bash
uv run python -m scripts.setup --extension dbt --origin http://localhost:3004 --handshake extensions/dbt/.env.bridge
docker compose up -d --wait                                  # registers the dbt Keycloak clients
docker compose -f compose.users.yaml up -d --wait users      # the user portal links to dbt
cd extensions/dbt
uv run python -m scripts.setup                               # writes .env for this extension
docker compose --profile images build
docker compose up -d --wait
```

Then a team administrator enables dbt for an environment, either through an agent
(`enable_environment`) or with `POST /api/v1/teams/{team}/environments/development`.

## Development

```bash
uv sync
uv run pytest && uv run ruff check .
uv run python -m scripts.api_contract    # after an API change; review contracts/dbt-api/v1/openapi.yaml
```

A run ends 15 minutes before its catalog token expires; `RUN_TIMEOUT_SECONDS` can only lower that. All runs share the Docker host of the extension; see the [Kubernetes design](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/dbt/DECISIONS.md#run-jobs-on-kubernetes-proposed-for-dbt-020-2026-10-07) for running them elsewhere.
