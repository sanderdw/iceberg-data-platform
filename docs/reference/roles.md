# Roles and permissions

A user holds one role per team, and that role applies to every database the team owns, in every environment. Access is the union over all of a user's teams. [Teams, environments and roles](../teams-and-environments.md) explains the model.

## Team roles

| Role | Portal label | Access to every database of that team |
| --- | --- | --- |
| `reader` | Read | Read namespaces, tables, views, semantic models and data |
| `writer` | Read & write | Also create and write namespaces, tables and views, and publish and delete semantic models |
| `admin` | Administrator | Also manage catalog access, create, rename and delete the team's databases, and manage data shares |

## Platform administration

Portal administration is the Keycloak client role `iceberg-admin/platform-admin`. No team role grants it, including Administrator. It is needed for the administration portal and its MCP server; see the [administration guide](../admin-guide.md).

## Recipients of a data share

| Recipient | Access |
| --- | --- |
| Another team | Its current and future members read the selected objects with their own accounts, whatever their role in that team |
| External party | The share's client credential reads the selected objects |
| Listed in a data catalog | Nothing: a listing publishes metadata only |

See [data shares](../data-shares.md).

## Extensions

Each extension checks the same team role. Only a team Administrator enables an extension for an environment; that creates the team's service account there.

| Extension | reader | writer | admin |
| --- | --- | --- | --- |
| [Conversational BI](../conversational-bi.md) | Ask questions about the team's models and the models shared with it | as reader | Also enable the environment |
| [dbt pipelines](../dbt.md) | View projects, files, runs, schedules and pipelines; compile, preview and test | Also create projects and branches, commit to branches, build in development, schedule development | Also merge to `main`, enable environments, schedule acceptance and production |
| [Ingestion (dlt)](../ingestion.md) | View pipelines, runs, logs and schedules; validate a definition | Also run pipelines and stop runs | Also create, change and delete pipelines, set secrets and schedules, enable environments |
| [OpenMetadata](../openmetadata.md) | - | - | List the team's data shares; OpenMetadata decides who sees them |
