# Install with one command

For the integrated Keycloak authentication, see [Keycloak setup](keycloak.md).
The same two Compose projects support Keycloak by default. The stable commands below
install the latest stable release; use the preview commands to test a branch.


First install and start Docker with Compose v2. On [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) and [Windows](https://docs.docker.com/desktop/setup/install/windows-install/), use Docker Desktop with Linux containers. Allocate at least 4 GiB of Docker memory for a small demonstration. Images support AMD64 and ARM64.

## Linux and macOS

Run in Terminal:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh
```

## Windows

Run in PowerShell:

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

The installer downloads and verifies the release bundle, creates `.env` with random credentials, pulls the images, and starts both Compose stacks. Notebook and reporting images are pulled too, ready for on-demand use. No source checkout, Python, uv, Node.js or local image builds are required.

The notebook image is browser-free and supports notebook execution and HTML/Jupyter
exports. PDF, thumbnail and screenshot exports are not included.

Configuration is stored in `~/iceberg-data-platform` on Linux/macOS and `$HOME\iceberg-data-platform` on Windows. The Compose files pin the portal, monitoring service, user portal, notebook runtime and reporting runtime to the installed release. Third-party data services retain their tested version tags. Report definitions persist in the workspace project's `reports-data` volume; back it up alongside shared notebook volumes. Stop the user gateway before copying this SQLite volume so its database and any WAL/SHM files stay consistent.

After installation:

- Open the administration portal at http://localhost:3000.
- Find `PLATFORM_ADMIN_USERNAME` and `PLATFORM_ADMIN_PASSWORD` in `.env`, sign in through Keycloak, and change the initial password.
- Create a team, database and user; then open the user portal at http://localhost:3002.

The scripts are available for inspection in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest). You can also download the installation archive there and run its `install.sh` or `install.ps1` to install that release.

## Install a specific version

The commands above always install the newest stable release. Every release from 0.3.0
also serves installers pinned to itself: its bundle, Compose files and application
images all carry that version. Replace `latest/download` with `download/vX.Y.Z`:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.4.1/install.sh | sh
```

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.4.1/install.ps1 | iex
```

## Test a branch

A branch publishes its own preview under `BRANCH-preview`, where `BRANCH` is the branch
name in lowercase with every character other than letters, digits, `.`, `_` and `-`
written as `-` (`feature/foo` becomes `feature-foo`). Once the branch's `Release`
workflow has published its first successful preview:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.sh | sh
```

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.ps1 | iex
```

The installer downloads one exact preview bundle, verifies its checksum and uses
the matching application images, including notebooks. Docker with Compose is enough;
users do not need Git, Python, Node.js or a GitHub login. Each successful publication
updates these download entrypoints. Rerun the command to install a newer preview;
updates are never automatic. The usual stable installation command stays unchanged.

Use a fresh installation. Both versions use `iceberg-platform`, `iceberg-workspaces`
and the same ports and volumes. A different installation directory does not isolate
them; use a separate Docker environment to test alongside an existing installation.
Preview updates within the same installation preserve `.env` and data. There is no
migration from an earlier password-based installation.

Open http://localhost:3000, use `PLATFORM_ADMIN_USERNAME` and `PLATFORM_ADMIN_PASSWORD`
from `.env`, and change the temporary password. Create a team, database and user, then
sign in at http://localhost:3002. See [account management](keycloak.md#manage-users-in-the-administration-portal).

## Choose a directory

Linux/macOS:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh -s -- --dir "$HOME/my-iceberg"
```

Windows PowerShell:

```powershell
$env:ICEBERG_INSTALL_DIR = "$HOME\my-iceberg"
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

Use a directory separate from a source checkout. Docker must have access to this directory for credential setup; Docker Desktop shares the home directory by default.

## Update or restart

Run the same installer command again. It preserves `.env` and data volumes, refreshes the managed configuration files (saving existing Compose files as `.bak`), pulls the images of the newest release, and starts the stacks. Save work and stop active notebook sessions before updating. Back up data and review the release notes for migration steps.

To restart without downloading anything, run these commands from the installation directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

Do not run `docker compose down --volumes` during an upgrade. The fixed project names reuse PostgreSQL, pgAdmin, RustFS and notebook volumes. Earlier experimental metadata formats are not migrated automatically.

This is a local development platform. Default ports bind to localhost; review the [security model](https://github.com/sanderdw/iceberg-data-platform/blob/main/SECURITY.md) before exposing services beyond a trusted local environment.
