# User portal

The user portal runs at http://localhost:3002, or on Kubernetes at `https://users.<domain>`. Team members sign in with Keycloak, pick a team and environment, and work with their databases in shared [marimo](https://marimo.io/) notebooks.

To open the portals of a Compose installation from a phone or another computer for a class or demo, use the [`quick-share` agent skill](agent-skills.md); a [Kubernetes installation](install-kubernetes.md) is reachable that way already. Sign-in needs HTTPS off `localhost`, so a plain port binding such as `compose.users.lan.yaml` is not enough on its own.

## Using the portal

The top menu has six sections, all scoped to the selected **Active team** and **Environment** (Development, Acceptance or Production). Your role is set per team, so you can write in one team and only read in another. The URL keeps your location, and lists refresh every 30 seconds.

- **Team overview:** the team's members and roles, its databases and your active notebooks.
- **Databases:** team Administrators create, rename and delete the team's databases. Renaming keeps the catalog ID and connection settings. Deletion is immediate and removes all data and shares, so you must type the name to confirm it. If cleanup fails, use **Resume deletion**. Moving a database to another team is a platform-administrator action.
- **Catalog:** browse namespaces, tables and views. **Details →** shows a table's schema, snapshots, branches and tags, partitioning, sort order and properties, or a view's SQL and versions. **Semantic models** are listed with them, with a diagram of their datasets and relationships; see [semantic models](semantic-models.md). **Load preview** reads up to 100 rows from any snapshot with your own permissions. Everything here is read-only.
  A table built by a pipeline, such as a dbt project, shows its description and a **Produced by** card: the last run, the source revision, the outcome of its tests, links to its upstream tables and a link back to the pipeline. Registered extensions also appear in the top navigation.
- **Notebooks:** open a database in marimo. Choose **Shared files** in the notebook selector to browse team notebooks and the examples. See [notebooks](notebooks.md).
- **Data shares:** see [data shares](data-shares.md).
- **Getting started:** three ways to work with your data: the portal, [your own tools](connect.md) and an [AI agent](ai-agents.md). It includes the commands for this installation, filled in for a database of the active team.

The API is documented at `/docs` on the portal (http://localhost:3002/docs with Compose), and **Try it out** works with your session and permissions.
