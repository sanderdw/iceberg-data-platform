# Resource model

Polaris is the single source of truth. Users, teams, databases and data shares are Polaris records, stored in the `polaris` PostgreSQL database. There is no separate application database. A fresh installation has no teams, users or databases.

| Concept | Polaris representation | Application properties |
| --- | --- | --- |
| User | Principal with a dedicated principal role | `portal.name`, `portal.memberships` |
| Team | Principal role marked `portal.kind=team` | `portal.name`, `portal.description` |
| Membership | Team IDs and the role per team, recorded on the user principal | `portal.memberships` (JSON: team ID → role) |
| Role assignment | Per catalog, the user's principal role gets the catalog role matching their role in the owning team | Polaris catalog-role grants |
| Database | Internal Iceberg catalog with a dedicated RustFS bucket | `portal.name`, `portal.team`, `portal.environment`, `portal.description`, `portal.bucket` |
| Data share | Principal marked `portal.kind=share`, with a principal role and a catalog role of the same name | `portal.database`, `portal.objects`, `portal.recipient-team`, `portal.external`, `portal.expires-at`, … |

Managed records carry `portal.managed-by=iceberg-portal-v2`.

| Data | Where it lives |
| --- | --- |
| Users, teams, shares, grants and catalogs | PostgreSQL `polaris` database |
| Iceberg metadata and Parquet data | RustFS, one bucket per database |
| Saved notebooks | A Docker volume per team and environment |
| Sessions and notebook state | Process and container memory |
| Service configuration and bootstrap credentials | `.env` |

## Users

A user signs in through Keycloak and is linked by exact issuer and subject to a Polaris principal with a stable `portal-<uuid>` ID. Usernames are 3–48 characters. They start with a lowercase letter and contain lowercase letters, digits, `_` or `-`. Application users are not PostgreSQL login roles.

## Teams and roles

A team has a stable `team-<uuid>` ID, a unique name and an optional description. A user belongs to one or more teams and holds one role per team. That role applies to every database the team owns.

| Role | Catalog role | Access |
| --- | --- | --- |
| `reader` | `reader` | Read metadata and table data |
| `writer` | `writer` | Also create and write namespaces, tables and views |
| `admin` | `admin` | Also manage catalog access, the team's databases and data shares |
| `bucket-admin` | `admin` | Also direct S3 credentials, whose policy covers the buckets of the teams where the user holds this role |

Effective access is the union across all of a user's teams. The active team and environment only select what the portal and notebooks show. Changing memberships or roles, or moving a database, synchronizes the affected grants and bucket policies.

For example, a user who is `writer` in `analytics` and `reader` in `operations` can write to the databases of `analytics` and read those of `operations`. If a database moves from `operations` to `analytics`, that user can then write to it.

Portal administration is a separate Keycloak role (`iceberg-admin/platform-admin`), and no team role grants it.

## Databases

A database is an Iceberg catalog, not a PostgreSQL database. It has a stable `db-<uuid>` catalog ID, a display name, an owning team, an environment (`development`, `acceptance` or `production`) and a dedicated bucket. Display names are unique within a team and environment.

Renaming a database or moving it to another team keeps its catalog ID, bucket and data. Deletion is immediate in every environment. It revokes the database's shares and removes all stored data, and an interrupted deletion can be resumed.

## Data shares

A data share gives another team, an external party, or both read access to selected tables and views of one database. The destinations are fixed at creation. A share has a stable `share-<uuid>` ID that names three Polaris records: a principal (the external credential), its principal role and a catalog role in the shared database.

- **Grants:** the catalog role holds exactly one grant per selected object: `TABLE_READ_DATA` on a table, `VIEW_READ_PROPERTIES` on a view. It holds nothing on namespaces or the catalog, so external clients load objects by their full names. For team shares, the catalog role is granted to each recipient member's principal role, including future members, and removed when a member leaves.
- **Views are not filters.** The recipient's engine reads a view's underlying tables, so those tables must be shared too, and the recipient can read them in full. A share needs at least one table.
- **Limits and drift:** a share holds at most 50 objects, and a database at most 20 shares. Grants follow the object itself, not its name, so a renamed object stays shared, while a dropped and recreated one does not. The portal shows the difference, and saving the share again fixes it.
- **Ownership:** a share belongs to its database. Every current Administrator or Database + bucket administrator of the owning team can manage it. A shared database can't move to another team until its shares are revoked.
- **Secret and expiry:** the client secret is shown once and never stored. **New secret** ends the previous one immediately. Revocation and expiry end issued tokens at once. The user gateway enforces expiry every 30 seconds, and every share listing enforces it too.
