# Changelog

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
