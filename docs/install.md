# Install a released version

Requirements: Docker Engine or Docker Desktop with Compose v2, a Linux AMD64 or ARM64 container host, and at least 4 GiB of Docker memory for a small demonstration. macOS and Windows users can run Linux containers with Docker Desktop; use a Bash-compatible terminal for the commands below.

## Download and start

Download the installation bundle and `SHA256SUMS` from the [GitHub release](https://github.com/sanderdw/iceberg-data-platform/releases). For version 0.2.0:

```bash
curl -fLO https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.2.0/iceberg-data-platform-0.2.0-install.tar.gz
curl -fLO https://github.com/sanderdw/iceberg-data-platform/releases/download/v0.2.0/SHA256SUMS
# Linux; on macOS use: shasum -a 256 --check --ignore-missing SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
tar -xzf iceberg-data-platform-0.2.0-install.tar.gz
cd iceberg-data-platform-0.2.0-install

# Generate .env using Python already included in the portal image.
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD,dst=/install" \
  --entrypoint python ghcr.io/sanderdw/iceberg-data-platform-portal:0.2.0 \
  /install/scripts/setup.py

docker compose pull
docker compose up -d --no-build --wait
docker compose -f compose.users.yaml --profile images pull
docker compose -f compose.users.yaml up -d --no-build --wait users
```

Open the administration portal at http://localhost:3000 and log in with `PORTAL_PASSWORD` from `.env`. The user portal is at http://localhost:3002. Create a team, database and user in the administration portal before logging in to the user portal.

The notebook image is pulled explicitly because the user portal starts notebook containers on demand. The installation bundle pins the portal, monitoring service, user portal and notebook runtime to the same version. No source checkout, local Python, uv, Node.js or image build is required.

The images are listed under [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform). Public packages can be pulled without logging in. If a newly published package is still private, the repository owner must change its package visibility to Public before anonymous installation works.

## Upgrade

Save work and stop active notebook sessions before upgrading. Back up your data volumes and `.env`. Extract the new installation bundle into a new directory, copy your existing `.env` into it with restrictive permissions, then repeat the pull/start commands above from that directory. The fixed Compose project names reuse existing PostgreSQL, pgAdmin, RustFS and notebook volumes. Setup preserves an existing `.env`.

Do not run `docker compose down --volumes` during an upgrade. Existing installations may require migration steps from the release notes. Version 0.2.0 supports a fresh installation and does not migrate earlier experimental metadata formats.

This is a local development platform. Default ports bind to localhost; review the [security model](https://github.com/sanderdw/iceberg-data-platform/blob/main/SECURITY.md) before exposing services beyond a trusted local environment.
