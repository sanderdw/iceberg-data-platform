# Platform context

This document describes the platform's users, teams, memberships, roles and database definitions. It documents the resource model, rather than listing accounts or credentials from a running installation.

The platform starts from a fresh PostgreSQL 18 setup. Polaris bootstraps its schema and platform administrator identity; teams, application users and catalogs are created through the administration portal.

## Source of truth and storage

The administration portal manages resources through the Apache Polaris management API. Polaris persists its records in the `polaris` PostgreSQL database, under `polaris_schema`. The application portals do not maintain a separate user or team database.

| Concept | Polaris representation | Application properties |
| --- | --- | --- |
| User | Principal with a dedicated principal role | `portal.name`, `portal.memberships` |
| Team | Principal role marked `portal.kind=team` | `portal.name`, `portal.description` |
| Membership | Team IDs and the role per team, recorded on the user principal | `portal.memberships`, a JSON-encoded object of team ID to role |
| Role assignment | User's principal role receives, per catalog, the catalog role that matches the user's role in the owning team | `portal.memberships`; Polaris catalog-role assignments and privilege grants enforce access |
| Database definition | Internal Iceberg catalog with a dedicated RustFS bucket | `portal.name`, `portal.team`, `portal.environment`, `portal.description`, `portal.bucket`, `default-base-location` |
| Data share | Principal marked `portal.kind=share`, with a principal role and a catalog role of the same name | `portal.name`, `portal.recipient`, `portal.description`, `portal.database`, `portal.objects`, `portal.expires-at`, `portal.created-by`, `portal.created-by-name` |

Managed team, user, data share and catalog records carry `portal.managed-by=iceberg-portal-v2`. Teams are represented as Polaris roles, so there is no application-specific `teams` SQL table.

PostgreSQL data persists in the Docker volume `iceberg-platform_postgres18-data`, mounted at `/var/lib/postgresql`. The database files are under `/var/lib/postgresql/18/docker`.

## Users

A user is a person who signs in through Keycloak. The gateway resolves the exact issuer and subject to a linked Polaris principal; catalog requests and notebooks use that person's Keycloak access token.

Each user has a stable internal ID such as `portal-<uuid>`, a globally unique display username, one or more team memberships, and one application role per membership. The dedicated principal role has the same internal name as the user principal. Application users are not PostgreSQL login roles.

Usernames are 3–48 characters, start with a lowercase letter, and contain lowercase letters, digits, underscores or hyphens. Issued credentials are presented during creation; this document contains no credentials.

## Teams and memberships

A team groups users and owns database definitions. It has a stable `team-<uuid>` ID, a globally unique name and an optional description. Renaming a team preserves its ID and relationships.

Users can belong to multiple teams and must retain at least one membership. Memberships are stored explicitly in `portal.memberships`, together with the role the user holds in each team. Team records describe ownership and grouping; the portal separately grants each user's principal role the appropriate catalog roles. A team membership is therefore not implemented as a direct assignment of the team's principal role to the user.

Effective catalog access is the union of databases owned by the user's teams, each with the role of its owning team. Creating a database, changing memberships or roles, or moving a database synchronizes the affected catalog-role assignments and, where applicable, RustFS bucket policies.

The active team and environment select the user's portal and notebook context. The user's credentials retain access granted through all their team memberships.

## Roles

A user has one application role per team. The role applies to every database that team owns, so the same person can administer one team's databases and only read another's. Changing the role for one team leaves the grants of the other teams untouched; a changed role is revoked before the new one is granted.

Principals written before per-team roles carry `portal.teams` and a single `portal.role`. They are read as that role in every team and are rewritten to `portal.memberships` the next time their access is saved. An older portal version cannot read the new property, so downgrading is not supported.

| Role | Portal label | Access |
| --- | --- | --- |
| `reader` | Read | List and read catalog, namespace, table and view metadata; read table data |
| `writer` | Read & write | Manage catalog contents, including creating and writing tables |
| `admin` | Administrator | Manage catalog contents, metadata and access |
| `bucket-admin` | Database + bucket administration | Catalog `admin` access plus separately issued RustFS credentials and a policy covering the buckets of the teams where the user holds this role |

Each catalog has `reader`, `writer` and `admin` catalog roles. The `bucket-admin` application role maps to the `admin` catalog role and adds direct S3 bucket access. A user has at most one S3 identity; its policy lists only buckets of teams with `bucket-admin`, and becomes deny-all when the last such membership goes. RustFS stores those S3 identities and policies; the user principal records the associated access-key ID in `portal.bucket-access-key`.

The platform administration portal requires the Keycloak `iceberg-admin/platform-admin` role. An application user's `admin` or `bucket-admin` role does not grant access to that portal.

## Database definitions

A database in the portal is an Iceberg catalog, not a separate PostgreSQL database. Each definition has a stable `db-<uuid>` catalog ID, display name, owning team, environment, optional description, dedicated RustFS bucket and S3 base location.

Supported environments are `development`, `acceptance` and `production`. A display name is unique within its team and environment, so the same name can appear in different teams or environments. Each catalog can contain multiple namespaces, tables and views.

Moving a database changes its owning team and updates access. Its catalog ID, environment, bucket and stored table files remain the same.

For example, a user with role `writer` in `analytics` and `reader` in `operations` receives write access to ready catalogs owned by `analytics` and read access to those owned by `operations`, across all three environments. Moving a catalog from `operations` to `analytics` raises that user's access to it from read to write. An `events` catalog in `analytics/development` and an `events` catalog in `analytics/production` have separate IDs and buckets.

## Data shares

A data share gives an external party read access to selected tables and views of one database. It is a machine identity, not a user: it has no memberships, never appears in user listings and cannot sign in to either portal.

Each share has a stable `share-<uuid>` ID that names three Polaris records: the principal whose client ID and secret the recipient uses, its principal role, and a catalog role inside the shared database. That catalog role holds exactly one grant per selected object: `TABLE_READ_DATA` on a table, `VIEW_READ_PROPERTIES` on a view. It holds nothing on the namespace or the catalog. Polaris does not filter listings per object, so the recipient cannot list namespaces, tables or views at all and loads the shared objects by their full names. Storage credentials are vended by Polaris per table, read-only and confined to that table's location.

`portal.objects` is the selection as the team administrator saved it: a JSON list of `{kind, namespace, name}`, at most 50 per share and 20 shares per database. Polaris binds grants to the table or view itself, not to its name. A renamed object therefore stays shared under its new name, and an object that was dropped and recreated is no longer shared. The portal compares the saved selection with the grants Polaris really holds and shows the difference; saving the selection again revokes what is no longer selected and then grants what is missing.

A view is only a definition. The recipient's engine reads the underlying tables with the same credential, so the tables a view reads must be part of the share, and the recipient can read those tables in full. A view in a share is a convenience, never a row or column filter. A share without a table is refused.

A share belongs to its database, and through it to the owning team; the team is not stored on the share. Every current Administrator or Database + bucket administrator of that team can manage it, including after the person who created it lost that role. A shared database cannot be moved to another team until its shares are revoked, so new owners never inherit external access. Deleting a database revokes its shares first.

Readers and writers can list shares for databases in their active team and environment.
The user portal disables management buttons for these roles; the API enforces the same
administrator requirement for every mutation. Listing a share does not reveal its secret.

The client secret is returned once, on creation and on **New secret**, and is never stored by the portal. A new secret keeps the client ID and ends the previous secret at once. Revocation removes the principal's only role first, which ends already issued tokens immediately, then the catalog role, the principal role and finally the principal, which serves as the marker for resuming an interrupted revocation. An expiry, when set, is enforced as a real revocation: the user gateway checks every 30 seconds, and every listing of shares in either portal revokes what has expired, what was left half revoked and what lost its database.

## Related data and inspection

| Data | Storage |
| --- | --- |
| Polaris users, teams, data shares, membership properties, grants and catalog records | PostgreSQL `polaris` database |
| Iceberg metadata files, manifests and Parquet data | RustFS object storage in catalog buckets |
| Direct S3 identities and bucket policies | RustFS |
| Saved notebook files | Shared Docker volumes per team/environment |
| Active portal sessions and notebook execution state | Process/container memory |
| Local service configuration and bootstrap credentials | Private `.env` |

In pgAdmin, open **Iceberg Platform → Polaris metadata → Databases → polaris → Schemas → polaris_schema → Tables** to inspect Polaris records. Use the administration portal for user, team and catalog changes so that grants and bucket policies are synchronized.

Implementation references: [resource validation](../server/models.py), [Polaris resource management](../server/polaris.py), [RustFS access management](../server/storage.py), [user authentication and directory](../user_portal/directory.py), and [Compose services and volumes](../compose.yaml). See the [administration guide](admin-guide.md) for setup and pgAdmin login details.
