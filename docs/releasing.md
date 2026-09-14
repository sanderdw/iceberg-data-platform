# Preparing a source release

The initial version is **0.2.0**. No remote repository, tag, registry image or public release is created automatically by the local tooling.

## Validate the source

```bash
uv sync --locked --all-groups
npm ci
npm run verify
npm run check:release
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
```

`check:release` validates an explicit source allowlist, project versions, Apache-2.0 metadata, shared-font notices and local Markdown links. It rejects symlinks, unexpected source files, workstation paths and any known local `.env` secret embedded in the public files. It does not claim to detect every possible credential; run a dedicated secret scanner as well.

Validate the running application from the same source:

```bash
docker compose up -d --build --wait
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
npx playwright install chromium
npm run test:e2e
npm run test:stack
npm run test:data
npm run test:examples
```

Dependency checks:

```bash
uv export --locked --all-groups --no-hashes --no-emit-project --format requirements-txt -o /tmp/iceberg-requirements.txt
uvx pip-audit==2.10.1 -r /tmp/iceberg-requirements.txt --no-deps --disable-pip
npm audit
```

## Build the reviewable release files

```bash
npm run release:source
```

The resulting `dist/` contains a deterministic source `.tar.gz`, `SHA256SUMS`, and `source-manifest.txt`. The archive includes the applications, Dockerfiles, lockfiles, tests, documentation, example notebooks, shared font files and their licenses. It excludes `.env`, environments, caches, personal data, notebook outputs, credentials, historical material and `dist/` itself. It can be built again from an extracted archive without needing Git.

Scan the extracted archive with Gitleaks before uploading. CI does this automatically and fails on findings. Review dependency findings and the source manifest. Do not upload the whole working directory or the private local archive that was separated during cleanup.

## Publication

After local review, create the public hosting repository and push the reviewed source. Enable private vulnerability reporting and branch protection, require the CI check, and review repository permissions. No maintainer account, email address or remote URL is assumed by these files.

Once the hosted CI has passed, update the changelog date, tag the reviewed commit and attach the checked source archive and checksum to the release. The manual `Release candidate` workflow prepares artifacts without publishing a GitHub release or uploading container images.

The CI workflows use immutable action revisions, a read-only default token and fresh development credentials generated on the runner. Never add production credentials to CI. Workflow integration tests run on the runner's own Docker daemon and clean their own stacks at the end.

## Compatibility

The platform starts from a fresh PostgreSQL 18 setup. Preserve PostgreSQL, pgAdmin, RustFS and shared team/environment notebook volumes when updating an installation. A destructive reset is a separate operator action and must never be part of an upgrade or release script. Container base images and dependencies need their own vulnerability review for a deployment; the source package's dependency audit does not certify the operating-system image layers.
