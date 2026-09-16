# Publishing a release

The current version is **0.2.1**. The `Release` workflow publishes three versioned container packages and source/installation archives when a matching `vX.Y.Z` tag is pushed. Local tooling only prepares artifacts.

## Publish the Keycloak branch for testers

Commit and push `keycloak`. The `Release` workflow calls the complete CI suite,
including Keycloak onboarding, user lifecycle, notebook access and native installer
tests, then builds images on AMD64 and ARM64. Publication waits for every check and
image build. Failed checks or image builds leave the previously published installer entrypoints in place.
No merge or stable version bump is required.

Each run publishes an exact prerelease named `keycloak-RUN_ID-ATTEMPT` at the tested
commit, with matching application image tags. The generated installers pin both the
release and the image tag. A small `keycloak-preview` prerelease holds the current
`install.sh` and `install.ps1` entrypoints. Those are updated only after the exact
build is available; an installer downloaded during an update still selects a complete
build. The entrypoint release notes link to the exact source/build release; its own
Git tag is not moved. Keep release assets mutable for this rolling entrypoint.

Share these commands after the first successful run:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/keycloak-preview/install.sh | sh
```

```powershell
irm https://github.com/sanderdw/iceberg-data-platform/releases/download/keycloak-preview/install.ps1 | iex
```

All previews are marked `--prerelease --latest=false` and never write `:latest` image
tags. Existing installations do not update by themselves. Users rerun the command
to receive a newer preview. Test on a fresh Docker environment: the two Compose
project names and ports are shared with stable installations, not a parallel stack.

The workflow verifies anonymous image access before publishing the preview. If this
fails, make all three repository packages Public in GitHub Packages and rerun the
workflow. Publishing the branch alone does not publish uncommitted local changes.

To prepare a pinned bundle locally without publishing anything:

```bash
uv run python -m scripts.release --build --install --release-tag keycloak-123-1 --image-tag keycloak-123-1
```

## Validate the source

```bash
uv sync --locked --all-groups
npm ci
npm run verify
npm run check:release
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
```

`check:release` validates an explicit source allowlist, project versions, Apache-2.0 metadata, shared-font notices and local Markdown links. It rejects symlinks, unexpected source files, workstation paths and any known local `.env` secret embedded in the public files. It does not claim to detect every possible credential; run a dedicated secret scanner as well.

Validate both projects with the [Keycloak integration suite](keycloak.md#verification-and-optional-demo-data).
CI checks fresh administrator onboarding before seeding optional demo fixtures, and
runs separate native API regression fixtures using test application factories.

Dependency checks:

```bash
uv export --locked --all-groups --no-hashes --no-emit-project --format requirements-txt -o /tmp/iceberg-requirements.txt
uvx pip-audit==2.10.1 -r /tmp/iceberg-requirements.txt --no-deps --disable-pip
npm audit
```

## Build the reviewable release files

```bash
uv run python -m scripts.release --build --install
```

The resulting `dist/` contains a deterministic source `.tar.gz`, `SHA256SUMS`, and `source-manifest.txt`. The archive includes the applications, Dockerfiles, lockfiles, tests, documentation, example notebooks, shared font files and their licenses. It excludes `.env`, environments, caches, personal data, notebook outputs, credentials, historical material and `dist/` itself. It can be built again from an extracted archive without needing Git.

Scan the extracted archive with Gitleaks before uploading. CI does this automatically and fails on findings. Review dependency findings and the source manifest. Do not upload the whole working directory or the private local archive that was separated during cleanup.

## Publication

1. Update the version in `pyproject.toml`, `package.json` and both version fields in `package-lock.json`. Update the changelog and installation examples.
2. Run the checks above, push the reviewed commit and wait for hosted CI to pass.
3. Tag that commit and push the tag:

   ```bash
   git tag -a v0.2.1 -m "Release 0.2.1"
   git push origin v0.2.1
   ```

The workflow rejects a tag that does not match the project version, runs verification and a source secret scan, then builds each image on native AMD64 and ARM64 runners. It publishes `ghcr.io/sanderdw/iceberg-data-platform-{portal,users,notebook}` with exact version tags (for example `0.2.0`), architecture tags and a `latest` alias. Only after every image builds does it publish the multi-platform tags and GitHub release with installation/source archives and `SHA256SUMS`. Installations follow `latest`; exact version tags remain available for manual pinning.

The Docker-only bundle is generated from the source Compose definitions, removes all build contexts, keeps bind mounts relative, and uses `:latest` consistently for all application images, including the notebook image used by the user portal. It includes setup and pgAdmin configuration without local credentials. The two Compose files preserve the platform/workspace split, with Keycloak in the platform project. The `install.sh` and `install.ps1` release assets download the matching bundle, verify its checksum, generate `.env`, pull images and start both stacks. Their stable URLs are `/releases/latest/download/install.sh` and `/releases/latest/download/install.ps1`.

On first publication, open each package's settings from [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and confirm visibility is **Public**. Change private packages to Public before sharing the installation command. Verify an anonymous pull of all three packages before announcing the release. Publishing uses the workflow's `GITHUB_TOKEN` with `packages: write`; no registry secret is needed. Images include the source repository label so GitHub links them to this repository. See [GitHub's container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

For a failed release, fix the cause before retrying the workflow on the same tag. Never move a published release tag to different source. The manual `Release candidate` workflow still prepares source artifacts without publishing.

The CI workflows use immutable action revisions, a read-only default token and fresh development credentials generated on the runner. Never add production credentials to CI. Workflow integration tests run on the runner's own Docker daemon and clean their own stacks at the end.

## Compatibility

The platform starts from a fresh PostgreSQL 18 setup. Preserve PostgreSQL, pgAdmin, RustFS and shared team/environment notebook volumes when updating an installation. A destructive reset is a separate operator action and must never be part of an upgrade or release script. Container base images and dependencies need their own vulnerability review for a deployment; the source package's dependency audit does not certify the operating-system image layers.
