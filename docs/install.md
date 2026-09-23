# Installation

Install and start Docker with Compose v2. On [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) and [Windows](https://docs.docker.com/desktop/setup/install/windows-install/), use Docker Desktop with Linux containers. Allocate at least 4 GiB of Docker memory. Images support AMD64 and ARM64.

## Install the latest release

```bash
# Linux / macOS
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh
```

```powershell
# Windows PowerShell
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

The installer does the following:

- downloads and verifies the release bundle
- creates `.env` with random credentials in `~/iceberg-data-platform` (`$HOME\iceberg-data-platform` on Windows)
- pulls the images and starts both Compose projects

You don't need a source checkout, Python or Node.js. Continue with the [first steps](../README.md#first-steps).

The installer scripts and archives are available in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest) for inspection.

## Install a specific version

Replace `latest/download` with `download/vX.Y.Z`. The bundle, Compose files and images are all pinned to that version:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.5.0/install.sh | sh
```

## Test a branch

Branches publish previews under `BRANCH-preview`. Here `BRANCH` is the branch name in lowercase, with other characters written as `-` (`feature/foo` becomes `feature-foo`). See [publishing a branch](releasing.md#publish-a-branch-for-testers).

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.sh | sh
```

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/download/BRANCH-preview/install.ps1 | iex
```

Rerun the command to get a newer preview. Installations never update automatically. Previews and stable releases share project names, ports and volumes, so use a separate Docker environment to run both.

## Choose a directory

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh -s -- --dir "$HOME/my-iceberg"
```

```powershell
$env:ICEBERG_INSTALL_DIR = "$HOME\my-iceberg"
irm https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.ps1 | iex
```

Use a directory outside a source checkout that Docker can access. Docker Desktop shares your home directory by default.

## Update or restart

To update, run the installer again. It keeps `.env` and the data volumes, saves the previous Compose files as `.bak`, and pulls the new images. Save your notebook work first, and read the release notes before updating.

To restart without downloading anything, run from the installation directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

## Stop and preserve data

Stop the workspaces first so the user portal can close its notebooks:

```bash
docker compose -f compose.users.yaml down
docker compose down
```

`down` keeps all data. `docker compose down --volumes` deletes the PostgreSQL, pgAdmin, RustFS and Keycloak data, so never use it during an upgrade. Team notebook files live in separate `iceberg-workspaces-work-*` volumes, and neither command removes them. Keep `.env` together with the volumes, and back both up before destructive maintenance.
