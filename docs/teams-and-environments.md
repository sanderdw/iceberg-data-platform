# Teams, environments and roles

Everything on the platform belongs to a team. This page explains how teams, environments, databases and roles fit together, and why. The exact permissions are in [roles and permissions](reference/roles.md).

## Teams own the data

A team owns databases, and people are members of teams. Nobody owns a database personally. When someone leaves, the data stays with the team; when a database moves to another team, the members of the new team get access with their role there.

The team that produces data also decides who else may read it, through [data shares](data-shares.md).

## A database is an Iceberg catalog

A database is not a PostgreSQL database. It is an Iceberg catalog in Apache Polaris, with its own bucket in RustFS. Inside it are namespaces, and inside those tables, views and [semantic models](semantic-models.md).

One bucket per database keeps the storage boundary the same as the access boundary: credentials that Polaris hands out for a table never reach another database's files. Renaming or moving a database keeps its catalog ID, bucket and connection settings, so notebooks, pipelines and shares keep working.

## Environments separate the life cycle

Each database is in one environment: Development, Acceptance or Production. A team can have databases with the same name in each, so a pipeline or notebook runs unchanged from development to production.

Environments also scope what runs for a team. Notebook files are shared per team and environment, and an extension acts through one service account per team and environment. A team Administrator enables each extension per environment, so a team can let pipelines write in development long before it lets them touch production.

## A role holds for the whole team

A member has one role per team: Read, Read & write or Administrator. That role applies to every database the team owns, in every environment. There are no per-database or per-table grants to keep track of; to give someone different access, put the data in another team's database or share it.

A person can be in several teams, with a different role in each: Read & write in `analytics` and Read in `operations`, for example. Access is the union of all of them.

The **Active team** and **Environment** in the user portal only choose what you see. Your credentials stay valid for all of your teams, so a notebook or tool on your computer can reach every database you have access to.

## Platform administration is separate

Platform administrators create teams and users on the administration portal. That is a separate Keycloak role, and no team role grants it. A team Administrator in turn creates the team's databases in the user portal as the team needs them, and manages its data shares, but never the platform.

## Everyone signs in the same way

People, notebooks, your own tools and AI agents all sign in through Keycloak and act as the user, with the same grants. Polaris checks those grants on every request. Extensions act through the team's service account, which only a team Administrator can enable. See the [security model](security.md).

How these records are stored in Polaris is described in the [resource model](develop/resource-model.md).
