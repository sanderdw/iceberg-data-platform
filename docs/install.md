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
- puts `iceberg_connect.py` next to `.env`, so you can [connect your own tools](connect.md) such as Python, the DuckDB CLI or DBeaver
- ends with where to sign in first: the administration portal, `platform-admin` and, on a new installation, the temporary password (you choose a new one at first sign-in). It also lists the commands to stop and start the platform and the bundled [agent skills](agent-skills.md)

The bundled `iceberg_connect.py` uses the default local addresses. If you changed `POLARIS_PUBLIC_URL` or the Keycloak address, for example with `quick-share`, download it from the user portal instead (**Catalog › Connect from your computer**). The portal fills in your addresses.

You don't need a source checkout, Python or Node.js. Continue with the [first steps](first-steps.md).

The installer scripts and archives are available in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest) for inspection.

## Add extensions

The installer can also add [Conversational BI](conversational-bi.md), a chat over your semantic models, [dbt pipelines](dbt.md), where AI agents build and test pipelines on your tables, and [Ingestion (dlt)](ingestion.md), which loads APIs, databases and files into them. In a terminal it asks for each one; `ICEBERG_EXTENSIONS=conversationalbi,dbt,ingestion` adds all three. See [extensions](extensions.md).

## Install a specific version

Replace `latest/download` with `download/vX.Y.Z`. The bundle, Compose files and images are all pinned to that version:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.9.1/install.sh | sh
```

## Test a branch

Branches publish previews under `BRANCH-preview`. Here `BRANCH` is the branch name in lowercase, with other characters written as `-` (`feature/foo` becomes `feature-foo`). See [publishing a branch](develop/releasing.md#publish-a-branch-for-testers).

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
docker compose -f conversationalbi/compose.yaml up -d --wait   # with Conversational BI
docker compose -f dbt/compose.yaml up -d --wait                # with dbt pipelines
docker compose -f ingestion/compose.yaml up -d --wait          # with Ingestion (dlt)
```

## Stop and preserve data

Stop the workspaces first so the user portal can close its notebooks:

```bash
docker compose -f conversationalbi/compose.yaml down   # with Conversational BI
docker compose -f dbt/compose.yaml down                # with dbt pipelines
docker compose -f ingestion/compose.yaml down          # with Ingestion (dlt)
docker compose -f compose.users.yaml down
docker compose down
```

`down` keeps all data. `docker compose down --volumes` deletes the PostgreSQL, pgAdmin, RustFS and Keycloak data, so never use it during an upgrade. Team notebook files live in separate `iceberg-workspaces-work-*` volumes, and neither command removes them. Keep `.env` together with the volumes, and back both up before destructive maintenance.
