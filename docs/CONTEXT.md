# Platform context

This document describes the platform's users, teams, memberships, roles and database definitions. It documents the resource model, rather than listing accounts or credentials from a running installation.

The platform starts from a fresh PostgreSQL 18 setup. Polaris bootstraps its schema and platform administrator identity; teams, application users and catalogs are created through the administration portal.

## Source of truth and storage

The administration portal manages resources through the Apache Polaris management API. Polaris persists its records in the `polaris` PostgreSQL database, under `polaris_schema`. The application portals do not maintain a separate user or team database.

| Concept | Polaris representation | Application properties |
| --- | --- | --- |
| User | Principal with a dedicated principal role | `portal.name`, `portal.teams`, `portal.role` |
| Team | Principal role marked `portal.kind=team` | `portal.name`, `portal.description` |
| Membership | Team IDs recorded on the user principal | `portal.teams`, a JSON-encoded list |
| Role assignment | User's principal role receives catalog roles for accessible catalogs | `portal.role`; Polaris catalog-role assignments and privilege grants enforce access |
| Database definition | Internal Iceberg catalog with a dedicated RustFS bucket | `portal.name`, `portal.team`, `portal.environment`, `portal.description`, `portal.bucket`, `default-base-location` |

Managed team, user and catalog records carry `portal.managed-by=iceberg-portal-v2`. Teams are represented as Polaris roles, so there is no application-specific `teams` SQL table.

PostgreSQL data persists in the Docker volume `iceberg-platform_postgres18-data`, mounted at `/var/lib/postgresql`. The database files are under `/var/lib/postgresql/18/docker`.

## Users

A user is a person who signs in through Keycloak. The gateway resolves the exact issuer and subject to a linked Polaris principal; catalog requests and notebooks use that person's Keycloak access token.

Each user has a stable internal ID such as `portal-<uuid>`, a globally unique display username, one or more team memberships, and one application role. The dedicated principal role has the same internal name as the user principal. Application users are not PostgreSQL login roles.

Usernames are 3–48 characters, start with a lowercase letter, and contain lowercase letters, digits, underscores or hyphens. Issued credentials are presented during creation; this document contains no credentials.

## Teams and memberships

A team groups users and owns database definitions. It has a stable `team-<uuid>` ID, a globally unique name and an optional description. Renaming a team preserves its ID and relationships.

Users can belong to multiple teams and must retain at least one membership. Memberships are stored explicitly in `portal.teams`. Team records describe ownership and grouping; the portal separately grants each user's principal role the appropriate catalog roles. A team membership is therefore not implemented as a direct assignment of the team's principal role to the user.

Effective catalog access is the union of databases owned by the user's teams. Creating a database, changing memberships or moving a database synchronizes the affected catalog-role assignments and, where applicable, RustFS bucket policies.

The active team and environment select the user's portal and notebook context. The user's credentials retain access granted through all their team memberships.

## Roles

A user has one application role across all accessible team databases; there are no separate per-team role selections.

| Role | Portal label | Access |
| --- | --- | --- |
| `reader` | Read | List and read catalog, namespace, table and view metadata; read table data |
| `writer` | Read & write | Manage catalog contents, including creating and writing tables |
| `admin` | Administrator | Manage catalog contents, metadata and access |
| `bucket-admin` | Database + bucket administration | Catalog `admin` access plus separately issued RustFS credentials and policies for the user's team buckets |

Each catalog has `reader`, `writer` and `admin` catalog roles. The `bucket-admin` application role maps to the `admin` catalog role and adds direct S3 bucket access. RustFS stores those S3 identities and policies; the user principal records the associated access-key ID in `portal.bucket-access-key`.

The platform administration portal requires the Keycloak `iceberg-admin/platform-admin` role. An application user's `admin` or `bucket-admin` role does not grant access to that portal.

## Database definitions

A database in the portal is an Iceberg catalog, not a separate PostgreSQL database. Each definition has a stable `db-<uuid>` catalog ID, display name, owning team, environment, optional description, dedicated RustFS bucket and S3 base location.

Supported environments are `development`, `acceptance` and `production`. A display name is unique within its team and environment, so the same name can appear in different teams or environments. Each catalog can contain multiple namespaces, tables and views.

Moving a database changes its owning team and updates access. Its catalog ID, environment, bucket and stored table files remain the same.

For example, a user with role `writer` in both `analytics` and `operations` receives write access to ready catalogs owned by either team, across all three environments. An `events` catalog in `analytics/development` and an `events` catalog in `analytics/production` have separate IDs and buckets.

## Related data and inspection

| Data | Storage |
| --- | --- |
| Polaris users, teams, membership properties, grants and catalog records | PostgreSQL `polaris` database |
| Iceberg metadata files, manifests and Parquet data | RustFS object storage in catalog buckets |
| Direct S3 identities and bucket policies | RustFS |
| Saved notebook files | Shared Docker volumes per team/environment |
| Active portal sessions and notebook execution state | Process/container memory |
| Local service configuration and bootstrap credentials | Private `.env` |

In pgAdmin, open **Iceberg Platform → Polaris metadata → Databases → polaris → Schemas → polaris_schema → Tables** to inspect Polaris records. Use the administration portal for user, team and catalog changes so that grants and bucket policies are synchronized.

Implementation references: [resource validation](../server/models.py), [Polaris resource management](../server/polaris.py), [RustFS access management](../server/storage.py), [user authentication and directory](../user_portal/directory.py), and [Compose services and volumes](../compose.yaml). See the [administration guide](admin-guide.md) for setup and pgAdmin login details.
