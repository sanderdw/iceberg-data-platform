# Services

The addresses of a [Compose installation](../install.md). Credentials are in `.env` in the installation directory.

| Service | URL | Login |
| --- | --- | --- |
| Administration portal | http://localhost:3000 | `platform-admin` + initial `PLATFORM_ADMIN_PASSWORD` |
| User portal | http://localhost:3002 | Keycloak account created or linked by an administrator |
| API documentation | `/docs` on either portal | Portal session |
| User MCP server | http://localhost:3002/mcp | Keycloak sign-in through the `iceberg-mcp` client, your own permissions |
| Administration MCP server | http://localhost:3000/mcp | Keycloak sign-in through the `iceberg-mcp` client, `platform-admin` role |
| Keycloak console | http://localhost:8080/admin | `admin` + `KEYCLOAK_ADMIN_PASSWORD` |
| Polaris Iceberg REST API | http://localhost:8181/api/catalog | Keycloak bearer token ([connect from your computer](../connect.md)) or client credentials |
| RustFS console | http://localhost:9001 | `RUSTFS_ACCESS_KEY` + `RUSTFS_SECRET_KEY` |
| pgAdmin | http://localhost:5050 | Optional ([pgAdmin](../admin-guide.md#pgadmin)); `PGADMIN_EMAIL` + `PGADMIN_PASSWORD` |
| Conversational BI | http://localhost:3007 | Optional [extension](../conversational-bi.md); Keycloak account |
| dbt pipelines | http://localhost:3004 | Optional [extension](../dbt.md); Keycloak account, MCP through the `ext-dbt-mcp` client |
| Ingestion (dlt) | http://localhost:3008 | Optional [extension](../ingestion.md); Keycloak account, MCP through the `ext-ingestion-mcp` client |
| OpenMetadata | http://localhost:3011 | Optional [extension](../openmetadata.md); opens your OpenMetadata server, sync status at `/api/status` |

All ports bind to `127.0.0.1`, and PostgreSQL is internal only. For access beyond localhost, see [deploying beyond localhost](../security.md#deploying-beyond-localhost) or the [quick-share skill](../agent-skills.md).

On Kubernetes every portal has its own HTTPS host, the credentials are in the Secret `iceberg-platform`, and pgAdmin and the RustFS console don't exist; see [addresses and sign-in](../install-kubernetes.md#addresses-and-sign-in).
