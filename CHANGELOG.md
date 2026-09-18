# Changelog

## Unreleased

- The `keycloak` branch publishes tested prereleases with one-command installers
  and matching image tags, independently of stable releases.
- Supported Keycloak OIDC integration for both portals, Polaris and per-user notebooks.
- Administration-portal account creation, explicit linking, temporary password reset and access revocation.
- Keycloak in the existing platform/workspace Compose split and standard application images, optional demo fixtures and dedicated integration CI.
- Source and Docker installation bundles include Keycloak setup and operating documentation.
- Roles are assigned per team: a user can hold a different role in each team, managed in one **Edit access** dialog. The user portal shows the role of the active team.
- Breaking: users are created and edited with `memberships: [{team, role}]`; `PATCH /api/users/{id}` replaces the full list and `PATCH /api/users/{id}/role` is removed. Existing users keep their access and are rewritten to the new `portal.memberships` property on their next edit. Downgrading to an older portal version is not supported afterwards.
- Two example notebooks write and read an Iceberg format-version 3 table with DuckDB's native Iceberg extension: `VARIANT`, `TIMESTAMP_NS`, `GEOMETRY`, default values, row lineage, deletion vectors and time travel. They run from top to bottom without controls.
- The DuckDB connection helper can attach writable and vend storage credentials for a table it just created.
- The table preview reads with DuckDB's Iceberg extension instead of PyIceberg, so Iceberg v3 tables with `variant` and `geometry` columns preview too. It stays an isolated, bounded process that cannot take the portal down; the user portal image no longer contains PyIceberg and PyArrow. The starter notebook explains when PyIceberg cannot read a v3 table.
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
