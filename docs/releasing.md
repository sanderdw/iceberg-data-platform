# Publishing a release

The current version is **0.5.0**. The `Release` workflow publishes four versioned container packages and source/installation archives when a matching `vX.Y.Z` tag on `main` is pushed. Branches publish their own previews. Local tooling only prepares artifacts.

## Versions and channels

| Channel | Published by | Release | Application images | Installation command |
| --- | --- | --- | --- | --- |
| Stable | `vX.Y.Z` tag on `main` | `vX.Y.Z`, marked Latest when it is the highest version | `:X.Y.Z` and `:latest` | `/releases/latest/download/install.sh` or `/releases/download/vX.Y.Z/install.sh` |
| Branch preview | push to a listed branch, or **Run workflow** on any branch | prerelease `SLUG-RUN_ID-ATTEMPT` plus the rolling `SLUG-preview` | `:SLUG-RUN_ID-ATTEMPT` | `/releases/download/SLUG-preview/install.sh` |

The project version lives in `pyproject.toml`, `package.json`, `package-lock.json`, `uv.lock` and the API title in `server/app.py`; `check:release` rejects any difference. Every published installer is pinned to its own release and image tag, so `/releases/latest/download/install.sh` is simply the installer of the newest stable release. Previews are prereleases, which GitHub never serves as Latest, and they never write `:latest` image tags. The workflow checks after each preview that the Latest release is unchanged and still downloads, and after each stable release that the latest command serves it.

## Publish a branch for testers

Branches listed under `on.push.branches` in `.github/workflows/release.yml` publish on
every push; add or remove a branch there. Any other branch publishes on demand:
**Actions → Release → Run workflow** and select the branch, or
`gh workflow run Release --ref BRANCH`. The workflow calls the complete CI suite,
including Keycloak onboarding, user lifecycle, notebook access and native installer
tests, then builds images on AMD64 and ARM64. Publication waits for every check and
image build. Failed checks or image builds leave the previously published installer entrypoints in place.
No merge or stable version bump is required.

`SLUG` is the branch name in lowercase with every character other than letters,
digits, `.`, `_` and `-` replaced by `-`, at most 60 characters (`feature/foo` becomes
`feature-foo`). Branch names starting with `v` and a digit are rejected to keep the
stable tag namespace free. Branches whose names reduce to the same slug share one
entrypoint.

Each run publishes an exact prerelease named `SLUG-RUN_ID-ATTEMPT` at the tested
commit, with matching application image tags. The generated installers pin both the
release and the image tag. A small `SLUG-preview` prerelease holds the current
`install.sh` and `install.ps1` entrypoints. Those are updated only after the exact
build is available; an installer downloaded during an update still selects a complete
build. The entrypoint release notes link to the exact source/build release; its own
Git tag is not moved. Keep release assets mutable for this rolling entrypoint.
The five newest exact builds of a branch are kept; older ones are deleted together with
their Git tags. Their container image versions remain in GitHub Packages. After a branch
is merged, delete its `SLUG-preview` release so the command stops serving the last build.

Share these commands after the first successful run, here for `keycloak`:

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
uv run python -m scripts.release --build --install --release-tag v0.5.0 --image-tag 0.5.0
```

The resulting `dist/` contains a deterministic source `.tar.gz`, `SHA256SUMS`, and `source-manifest.txt`. The archive includes the applications, Dockerfiles, lockfiles, tests, documentation, example notebooks, shared font files and their licenses. It excludes `.env`, environments, caches, personal data, notebook outputs, credentials, historical material and `dist/` itself. It can be built again from an extracted archive without needing Git.

Scan the extracted archive with Gitleaks before uploading. CI does this automatically and fails on findings. Review dependency findings and the source manifest. Do not upload the whole working directory or the private local archive that was separated during cleanup.

## Publication

1. Update the version in `pyproject.toml`, `package.json`, both version fields in `package-lock.json`, `uv.lock` (`uv lock`) and `server/app.py`. Update the changelog and installation examples.
2. Run the checks above, merge the reviewed commit into `main` and wait for hosted CI to pass.
3. Tag that commit on `main` and push the tag:

   ```bash
   git tag -a v0.5.0 -m "Release 0.5.0"
   git push origin v0.5.0
   ```

The workflow rejects a tag that does not match the project version or is not on `main`, runs verification and a source secret scan, then builds each image on native AMD64 and ARM64 runners. It publishes `ghcr.io/sanderdw/iceberg-data-platform-{portal,users,notebook,reporting}` with exact version tags (for example `0.5.0`), architecture tags and a `latest` alias. Only after every image builds does it publish the multi-platform tags and GitHub release with installation/source archives and `SHA256SUMS`. The release is marked Latest only when it is the highest version, so a fix on an older line never takes over the installation command. The workflow then downloads `/releases/latest/download/install.sh` and `install.ps1` and fails unless they are pinned to the new version.

The notebook image omits browser-export dependencies and supports HTML/Jupyter exports. CI tests startup and exports offline with a read-only root filesystem. The image is published for AMD64 and ARM64.

The Docker-only bundle is generated from the source Compose definitions, removes all build contexts, keeps bind mounts relative, and pins all application images, including the notebook image used by the user portal, to the released version. It includes setup and pgAdmin configuration without local credentials. The two Compose files preserve the platform/workspace split, with Keycloak in the platform project. The `install.sh` and `install.ps1` release assets download the matching bundle, verify its checksum, generate `.env`, pull images and start both stacks. Their stable URLs are `/releases/latest/download/install.sh` and `/releases/latest/download/install.ps1`; `/releases/download/vX.Y.Z/` serves the same installers for one exact version. The unpinned `install.sh` and `install.ps1` in the source tree are templates that follow `latest`; the build pins them.

On first publication, open each package's settings from [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and confirm visibility is **Public**. Change private packages to Public before sharing the installation command. Verify an anonymous pull of all three packages before announcing the release. Publishing uses the workflow's `GITHUB_TOKEN` with `packages: write`; no registry secret is needed. Images include the source repository label so GitHub links them to this repository. See [GitHub's container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

For a failed release, fix the cause before retrying the workflow on the same tag. Never move a published release tag to different source. The manual `Release candidate` workflow still prepares source artifacts without publishing.

The CI workflows use immutable action revisions, a read-only default token and fresh development credentials generated on the runner. Never add production credentials to CI. Workflow integration tests run on the runner's own Docker daemon and clean their own stacks at the end.

## Compatibility

The platform starts from a fresh PostgreSQL 18 setup. Preserve PostgreSQL, pgAdmin, RustFS and shared team/environment notebook volumes when updating an installation. A destructive reset is a separate operator action and must never be part of an upgrade or release script. Container base images and dependencies need their own vulnerability review for a deployment; the source package's dependency audit does not certify the operating-system image layers.
