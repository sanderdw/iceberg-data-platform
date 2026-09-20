# Changelog

## 0.4.0 — 2026-09-20

- Data shares: a team's Administrator shares selected tables and views of a database with an external party from the user portal, without a portal administrator. Each share has its own Polaris client ID and secret, shown once with **Copy DuckDB snippet**, an optional expiry, **New secret** and **Revoke**. The copied Python script runs with `uv`, lists every shared table and view in comments, and queries the first table; the UI does not display the code. The recipient reads exactly the selected objects and cannot list, write or reach any other table or database; vended storage credentials are read-only and confined to the shared table. A view shares only its definition, so its tables must be shared with it.
- The user portal has a consistent top menu for **Catalog**, **Notebooks** and **Data shares**. Readers and writers can see shares for their active team and environment, with disabled management buttons explaining the required administrator role.
- Both portals preserve navigation in the URL across reloads and Back/Forward. Lists refresh every 30 seconds while visible and on returning to the tab, preserving forms and open notebooks. User creation has more space below the account-mode buttons.
- Keycloak access tokens renew automatically within an eight-hour portal session and Keycloak's session limits. Notebook helpers retrieve the current user token from the gateway without receiving refresh tokens; existing DuckDB attachments need reconnecting to refresh cached credentials. Sign-in returns to the requested portal location.
- The administration portal lists every data share on a new **Data shares** page and can revoke it. Deleting a database revokes its shares.
- Breaking: a database with data shares cannot be moved to another team until the shares are revoked.
- Concurrent share creation and database moves recheck persisted move state before activating a share. Shares expiring today remain editable without changing their expiry. Notebook token renewal reads the private token file written by the runtime entrypoint.
- Upgrade note: the user gateway now also writes to Polaris with the platform identity, to manage data shares. `compose.users.yaml` passes `POLARIS_PUBLIC_URL` and `S3_ENDPOINT` to it. Sharing with parties outside this machine needs your own TLS reverse proxy for the Polaris catalog API and RustFS, with both variables set before the shared databases are created; see `SECURITY.md`.

## 0.3.1 — 2026-09-19

- Open notebooks are grouped under the `iceberg-workspaces` stack in Docker tools and listed with their CPU and memory on the **Infrastructure** page. Compose commands leave them alone; the user portal still starts and removes them.

## 0.3.0 — 2026-09-19

- Upgrade note: this release signs in through Keycloak and there is no migration from a password-based 0.2 installation. Rerunning the latest installation command on a 0.2 installation moves it to 0.3.0; start from a fresh installation instead.
- Stable installers, Compose files and application images are pinned to their release. `/releases/latest/download/install.sh` installs the newest stable release from `main`; `/releases/download/vX.Y.Z/install.sh` installs exactly that version.
- Any branch can publish tested prereleases with one-command installers and matching
  image tags under `BRANCH-preview`, independently of stable releases: on every push
  for branches listed in the `Release` workflow, on demand for all others. The five
  newest builds of a branch are kept.
- Stable tags must be on `main`, only the highest version is marked Latest, and the workflow verifies the latest installation command after every publication.
- Supported Keycloak OIDC integration for both portals, Polaris and per-user notebooks.
- Administration-portal account creation, explicit linking, temporary password reset and access revocation.
- Keycloak in the existing platform/workspace Compose split and standard application images, optional demo fixtures and dedicated integration CI.
- Source and Docker installation bundles include Keycloak setup and operating documentation.
- Roles are assigned per team: a user can hold a different role in each team, managed in one **Edit access** dialog. The user portal shows the role of the active team.
- Breaking: users are created and edited with `memberships: [{team, role}]`; `PATCH /api/users/{id}` replaces the full list and `PATCH /api/users/{id}/role` is removed. Existing users keep their access and are rewritten to the new `portal.memberships` property on their next edit. Downgrading to an older portal version is not supported afterwards.
- Two example notebooks write and read an Iceberg format-version 3 table with DuckDB's native Iceberg extension: `VARIANT`, `TIMESTAMP_NS`, `GEOMETRY`, default values, row lineage, deletion vectors and time travel. They run from top to bottom without controls. The writing example replaces only a table it created itself, recorded in a table property.
- The DuckDB connection helper can attach writable and vend storage credentials for a table it just created.
- The table preview reads with DuckDB's Iceberg extension instead of PyIceberg, so Iceberg v3 tables with `variant` and `geometry` columns preview too. It stays an isolated, bounded process that cannot take the portal down; the user portal image no longer contains PyIceberg and PyArrow. The starter notebook explains when PyIceberg cannot read a v3 table.
- Both images log the DuckDB extension builds they installed and keep them in `/opt/duckdb/extensions/VERSIONS`, because extensions are not pinned by `uv.lock`.
- `FORWARDED_ALLOW_IPS` reaches both portals, so sign-in limits apply per visitor behind a reverse proxy.
- RustFS 1.0.0 replaces the 1.0.0-rc.6 release candidate; existing `rustfs-data` volumes are kept.

## 0.2.1 — 2026-09-16

- One-command installers for Linux/macOS and Windows PowerShell: download and verify configuration, generate `.env`, pull images, and start both stacks automatically.
- Installation Compose files use `:latest` for all application images, including on-demand notebooks.
- Rerunning the installer preserves credentials and data volumes and backs up the existing Compose files.

## 0.2.0 — 2026-09-15

Initial public release:

- Versioned GitHub container packages for AMD64 and ARM64, plus a Docker-only installation bundle with checksums.

- Edit existing user roles, including catalog permission changes, S3 access promotion/demotion and rollback on provider failures.

- Development, Acceptance and Production environments, with database names scoped to team and environment.
- One shared notebook filespace per team/environment, concurrent member access and separate execution using each member's credentials. Fresh setup only; no migration of earlier user-specific workspaces.

- Nothing-inspired user portal with persistent dark/light themes, monochrome controls, Doto headlines and inline status messages.

- English interface, API messages, documentation and notebook examples throughout.
- Compose projects named `iceberg-platform` for administration and data services and `iceberg-workspaces` for user notebooks.

- FastAPI administration portal with central team management, multi-team users, movable databases and complete database deletion.
- Portal-administrator catalog explorer for namespaces, tables and views.
- Separate user portal with existing username/client-secret login and team selection.
- Isolated, persistent marimo workspaces with two synthetic-energy examples using PyIceberg and DuckDB.
- uv lockfile, pinned Python version, separate Compose stacks, authorization and browser tests.
- Apache-2.0 license, third-party font notices, contributor/security documentation, CI and a checked source-release builder.

No migration from earlier experimental metadata formats is provided. The public release uses the `iceberg-portal-v2` resource marker.
