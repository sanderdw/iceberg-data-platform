# First steps

1. Open the administration portal at http://localhost:3000, sign in as `platform-admin` and change the temporary password. The installer prints it, and it is `PLATFORM_ADMIN_PASSWORD` in `.env`.
2. Create a team, a database for that team, and a user in that team with the **Read & write** role. The [administration guide](admin-guide.md) explains each step.
3. Open the user portal at http://localhost:3002, sign in as that user with the one-time password, and change it.
4. Open **Notebooks** and pick an example from the notebook selector. Start with **01**, which creates the data that the later examples read. See [notebooks](notebooks.md).

## Services

| Service | URL | Login |
| --- | --- | --- |
| Administration portal | http://localhost:3000 | `platform-admin` + initial `PLATFORM_ADMIN_PASSWORD` |
| User portal | http://localhost:3002 | Keycloak account created or linked by an administrator |
| API documentation | `/docs` on either portal | Portal session |
| User MCP server | http://localhost:3002/mcp | Keycloak sign-in through the `iceberg-mcp` client, your own permissions |
| Administration MCP server | http://localhost:3000/mcp | Keycloak sign-in through the `iceberg-mcp` client, `platform-admin` role |
| Keycloak console | http://localhost:8080/admin | `admin` + `KEYCLOAK_ADMIN_PASSWORD` |
| Polaris Iceberg REST API | http://localhost:8181/api/catalog | Keycloak bearer token ([connect from your computer](connect.md)) or client credentials |
| RustFS console | http://localhost:9001 | `RUSTFS_ACCESS_KEY` + `RUSTFS_SECRET_KEY` |
| pgAdmin | http://localhost:5050 | `PGADMIN_EMAIL` + `PGADMIN_PASSWORD` |
| Conversational BI | http://localhost:3007 | Optional [extension](conversational-bi.md); Keycloak account |
| dbt pipelines | http://localhost:3004 | Optional [extension](dbt.md); Keycloak account, MCP through the `ext-dbt-mcp` client |
| Ingestion (dlt) | http://localhost:3008 | Optional [extension](ingestion.md); Keycloak account, MCP through the `ext-ingestion-mcp` client |

Credentials are in `.env` in the installation directory. All ports bind to `127.0.0.1`, and PostgreSQL is internal only. For access beyond localhost, see [deploying beyond localhost](security.md#deploying-beyond-localhost) or the [quick-share skill](agent-skills.md).

## Next

- Work with your data in the [user portal](user-portal.md), from [your own tools](connect.md) or through an [AI agent](ai-agents.md).
- Set up a class with the [agent skills](agent-skills.md).
- Add [extensions](extensions.md): Conversational BI, dbt pipelines and Ingestion (dlt).
