# Install with one command

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

The installer downloads and verifies the release bundle, creates `.env` with random credentials, pulls the images, and starts both Compose stacks. The notebook image is pulled too, ready for the first workspace. No source checkout, Python, uv, Node.js or local image builds are required.

Configuration is stored in `~/iceberg-data-platform` on Linux/macOS and `$HOME\iceberg-data-platform` on Windows. The Compose files use `:latest` for the portal, monitoring service, user portal and notebook runtime. Third-party data services retain their tested version tags.

After installation:

- Open the administration portal at http://localhost:3000.
- Find `PORTAL_PASSWORD` in the installation directory's `.env` and use it to log in.
- Create a team, database and user; then open the user portal at http://localhost:3002.

The scripts are available for inspection in the [release assets](https://github.com/sanderdw/iceberg-data-platform/releases/latest). You can also download the installation archive there and run its `install.sh` or `install.ps1` to install the latest release.

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

Run the same installer command again. It preserves `.env` and data volumes, refreshes the managed configuration files (saving existing Compose files as `.bak`), pulls `:latest`, and starts the stacks. Save work and stop active notebook sessions before updating. Back up data and review the release notes for migration steps.

To restart without downloading anything, run these commands from the installation directory:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

Do not run `docker compose down --volumes` during an upgrade. The fixed project names reuse PostgreSQL, pgAdmin, RustFS and notebook volumes. Earlier experimental metadata formats are not migrated automatically.

This is a local development platform. Default ports bind to localhost; review the [security model](https://github.com/sanderdw/iceberg-data-platform/blob/main/SECURITY.md) before exposing services beyond a trusted local environment.
