# User portal

The user portal runs at http://localhost:3002, or on Kubernetes at `https://users.<domain>`. Team members sign in with Keycloak, pick a team and environment, and work with their databases in shared [marimo](https://marimo.io/) notebooks.

A [Kubernetes installation](install-kubernetes.md) is reachable from any phone or computer; for a class, see [run a class](run-a-class.md). For a short demo of a Compose installation, the [`quick-share` agent skill](agent-skills.md) opens the portals through Cloudflare quick tunnels. Sign-in needs HTTPS off `localhost`, so a plain port binding such as `compose.users.lan.yaml` is not enough on its own.

## Using the portal

The top menu has six sections. The bar under it names the page you are on and holds **Team** and **Environment** (Development, Acceptance or Production): every page shows that team in that environment. Your role is set per team, so you can write in one team and only read in another. The URL keeps your location, and lists refresh every 30 seconds.

- **Team overview:** the team's members and roles, its databases and your active notebooks.
- **Databases:** team Administrators create, rename and delete the team's databases. Renaming keeps the catalog ID and connection settings. Deletion is immediate and removes all data and shares, so you must type the name to confirm it. If cleanup fails, use **Resume deletion**. Moving a database to another team is a platform-administrator action.
- **Catalog:** browse namespaces, tables and views. **Details →** shows a table's schema, snapshots, branches and tags, partitioning, sort order and properties, or a view's SQL and versions. **Semantic models** are listed with them, with a diagram of their datasets and relationships; see [semantic models](semantic-models.md). **Load preview** reads up to 100 rows from any snapshot with your own permissions. Everything here is read-only.
  A table built by a pipeline, such as a dbt project, shows its description and a **Produced by** card: the last run, the source revision, the outcome of its tests, links to its upstream tables and a link back to the pipeline.
- **Notebooks:** open a database in marimo. One bar on top of the notebook has the notebook picker, with your team's notebooks and the examples, and **New** for a notebook of your own. See [notebooks](notebooks.md).
- **Data shares:** see [data shares](data-shares.md).
- **Getting started:** three ways to work with your data: the portal, [your own tools](connect.md) and an [AI agent](ai-agents.md). It includes the commands for this installation, filled in for a database of the active team.

**Apps** in the top menu lists the registered [extensions](extensions.md), such as Pipelines and Conversational BI, each with a line on what it is for. Each opens in a new tab and has the same menu, with the portal's pages in the order of the top menu and the other extensions. Your username opens the account menu, with the theme switch and **Sign out**.

The API is documented at `/docs` on the portal (http://localhost:3002/docs with Compose), and **Try it out** works with your session and permissions.
