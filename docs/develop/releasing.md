# Publishing a release

The `Release` workflow publishes three container images (`ghcr.io/sanderdw/iceberg-data-platform-{portal,users,notebook}`), the Helm chart (`oci://ghcr.io/sanderdw/charts/iceberg-data-platform`) and the source and installation archives. Local tooling only prepares artifacts.

| Channel | Published by | Release | Image tags | Installer |
| --- | --- | --- | --- | --- |
| Stable | `vX.Y.Z` tag on `main` | `vX.Y.Z`, marked Latest if highest | `:X.Y.Z`, `:latest` | `/releases/latest/download/install.sh` or `/releases/download/vX.Y.Z/install.sh` |
| Branch preview | **Run workflow** on the branch | Prerelease `SLUG-RUN_ID-ATTEMPT` plus rolling `SLUG-preview` | `:SLUG-RUN_ID-ATTEMPT` | `/releases/download/SLUG-preview/install.sh` |

Every installer is pinned to its own release and image tag. Previews never become Latest and never write `:latest`. A preview also pushes the chart as version `X.Y.Z-SLUG-RUN_ID-ATTEMPT`, with any `.` or `_` in the slug turned into `-` because chart versions are SemVer. Its images default to the preview's tag: `helm install ... --version X.Y.Z-SLUG-RUN_ID-ATTEMPT`.

## Publish a branch for testers

A branch publishes on demand, either through **Actions → Release → Run workflow** or with `gh workflow run Release --ref BRANCH`. To publish a branch on every push, add it under `on.push.branches` in `.github/workflows/release.yml`.

The workflow runs the full CI suite and builds AMD64 and ARM64 images. Only then does it publish the exact prerelease and update the `SLUG-preview` installers. A failed run leaves the previous preview in place.

A preview also builds the Conversational BI, dbt and Ingestion (dlt) images from the same commit, tagged `:SLUG-RUN_ID-ATTEMPT`, so its installer can add all three extensions without their own releases. The platform installer doesn't add OpenMetadata, so a core preview doesn't build its image; an [OpenMetadata preview](#release-an-extension) publishes its own installer.

`SLUG` is the branch name in lowercase, with characters other than letters, digits, `.`, `_` and `-` replaced by `-`, up to 60 characters. Branch names that start with `v` plus a digit are rejected. The workflow keeps the five newest builds per branch. Delete the `SLUG-preview` release after merging.

To build a pinned bundle locally without publishing:

```bash
uv run python -m scripts.release --build --install --release-tag BRANCH-123-1 --image-tag BRANCH-123-1 --extension-image-tag BRANCH-123-1
```

## Publish a stable release

1. Update the version in `pyproject.toml`, `package.json`, both fields in `package-lock.json`, `uv.lock` (`uv lock`) and the API versions in `server/app.py` and `user_portal/app.py`, and `version` and `appVersion` in `deploy/helm/iceberg-data-platform/Chart.yaml`. `npm run check:release` rejects any mismatch. Update `docs/changelog.md`, add the release notes in `docs/releases/X.Y.Z.md` and to `nav` in `zensical.toml` and update the version in the install examples, including the chart versions (the platform's and the extensions') in `docs/install-kubernetes.md`, the release notes and `.agents/skills/kubernetes-install/SKILL.md`. Record Bridge contract changes in `contracts/bridge/CHANGELOG.md` and the version in `server/bridge.py` (see [COMPATIBILITY.md](https://github.com/sanderdw/iceberg-data-platform/blob/main/contracts/bridge/COMPATIBILITY.md)); the Bridge has no release of its own.
2. Run the [checks](contributing.md#tests) and the dependency audits:

   ```bash
   uv export --locked --all-groups --no-hashes --no-emit-project --format requirements-txt -o /tmp/iceberg-requirements.txt
   uvx pip-audit==2.10.1 -r /tmp/iceberg-requirements.txt --no-deps --disable-pip
   npm audit
   ```

3. Merge into `main`, wait for CI, then [release each extension](#release-an-extension) whose version changed. The installation bundle installs the extensions from their own releases, so a stable core release requires, for each one, the tag `<id>-v<version>` of the version in its `pyproject.toml`, unchanged bundled files since that tag, and public images:
   - Conversational BI: `conversationalbi-v<version>`, with `compose.yaml` and `scripts/setup.py` in `extensions/conversationalbi`.
   - dbt: `dbt-v<version>`, with `compose.yaml`, `scripts/setup.py` and `skills/` in `extensions/dbt`.
   - Ingestion (dlt): `ingestion-v<version>`, with `compose.yaml`, `scripts/setup.py` and `skills/` in `extensions/ingestion`.

   A branch preview skips this check, because it builds the extension images itself. OpenMetadata isn't in the installation bundle; it has its own installer, so this check doesn't cover it. Release it before the core when its version changed, so the core's release notes can point to it.
4. Tag the core commit on `main`:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

The workflow rejects a tag that doesn't match the project version or isn't on `main`. It then:

1. runs the checks and a Gitleaks scan of the source archive
2. builds the images on native AMD64 and ARM64
3. packages the Helm chart, pushes it to `oci://ghcr.io/sanderdw/charts/iceberg-data-platform` and publishes the GitHub release with the archives, the chart and `SHA256SUMS`. A new chart package starts private on GHCR, like a new image: make it public once and rerun the job
4. confirms that the `latest` installer URLs serve the new version

To check a release before merging, run **Actions → Release candidate → Run workflow**. It runs `npm run verify`, builds the source archive, scans it with Gitleaks and publishes nothing.

Never move a published tag. Fix the cause and rerun the workflow instead.

To inspect the artifacts locally, run `uv run python -m scripts.release --build --install --release-tag vX.Y.Z --image-tag X.Y.Z`. This writes a deterministic source archive, an install bundle and checksums to `dist/`. `check:release` enforces a source allowlist, matching versions, license notices and valid local Markdown links. It also rejects known local secrets.

## Release an extension

Extensions are versioned and released on their own, with tags `<id>-vX.Y.Z` that never mark the latest release. For Conversational BI:

1. In `extensions/conversationalbi`, update the version in `pyproject.toml`, `uv.lock` (`uv lock`), `conversationalbi/config.py`, `web/package.json` and `runtime/package.json` with their lock files, the two image tags in `compose.yaml` and the API contract (`uv run python -m scripts.api_contract`), and `version` and `appVersion` in `deploy/helm/iceberg-conversationalbi/Chart.yaml`. Date its `CHANGELOG.md` section. `uv run python -m scripts.release` rejects a mismatch.
2. After the merge into `main`, tag that commit:

   ```bash
   git tag -a conversationalbi-vX.Y.Z -m "Conversational BI X.Y.Z"
   git push origin conversationalbi-vX.Y.Z
   ```

The **Conversational BI release** workflow runs its CI, builds `ghcr.io/sanderdw/iceberg-conversationalbi` and `iceberg-conversationalbi-runtime` for AMD64 and ARM64, checks that both are public and publishes the release with its source archive and its Helm chart, which it also pushes to `oci://ghcr.io/sanderdw/charts/iceberg-conversationalbi`. On the first publication, set both packages to **Public** and rerun the failed job. Then release the core.

To publish Conversational BI from a branch without a release, run **Actions → Conversational BI release → Run workflow** on that branch, or `gh workflow run "Conversational BI release" --ref BRANCH`. It tags both images `:SLUG-RUN_ID-ATTEMPT` and publishes a prerelease `conversationalbi-SLUG-RUN_ID-ATTEMPT` with the source archive, never the version tag. It keeps the five newest previews per branch.

The dbt extension works the same way:

1. In `extensions/dbt`, update the version in `pyproject.toml`, `uv.lock` (`uv lock`), `dbt_portal/config.py`, the image tags in `compose.yaml` (both `iceberg-dbt` lines, the runner image and the `RUNNER_IMAGE` default) and the API contract (`uv run python -m scripts.api_contract`), and `version` and `appVersion` in `deploy/helm/iceberg-dbt/Chart.yaml`. Date its `CHANGELOG.md` section. `uv run python -m scripts.release` rejects a mismatch.
2. After the merge into `main`, tag that commit:

   ```bash
   git tag -a dbt-vX.Y.Z -m "dbt extension X.Y.Z"
   git push origin dbt-vX.Y.Z
   ```

The **dbt extension release** workflow runs its CI, builds `ghcr.io/sanderdw/iceberg-dbt` and `iceberg-dbt-runner` for AMD64 and ARM64, checks that both are public and publishes the release with its source archive and its Helm chart, which it also pushes to `oci://ghcr.io/sanderdw/charts/iceberg-dbt`. On the first publication, set both packages to **Public** and rerun the failed job. Its branch previews are named `dbt-SLUG-RUN_ID-ATTEMPT`.

The Ingestion extension works the same way:

1. In `extensions/ingestion`, update the version in `pyproject.toml`, `uv.lock` (`uv lock`), `ingestion/config.py`, the image tags in `compose.yaml` (both `iceberg-ingestion` lines, the runner image and the `RUNNER_IMAGE` default) and the API contract (`uv run python -m scripts.api_contract`), and `version` and `appVersion` in `deploy/helm/iceberg-ingestion/Chart.yaml`. Date its `CHANGELOG.md` section. `uv run python -m scripts.release` rejects a mismatch.
2. After the merge into `main`, tag that commit:

   ```bash
   git tag -a ingestion-vX.Y.Z -m "Ingestion (dlt) X.Y.Z"
   git push origin ingestion-vX.Y.Z
   ```

The **Ingestion release** workflow runs its CI, builds `ghcr.io/sanderdw/iceberg-ingestion` and `iceberg-ingestion-runner` for AMD64 and ARM64, checks that both are public and publishes the release with its source archive and its Helm chart, which it also pushes to `oci://ghcr.io/sanderdw/charts/iceberg-ingestion`. On the first publication, set both packages to **Public** and rerun the failed job. Its branch previews are named `ingestion-SLUG-RUN_ID-ATTEMPT`.

The OpenMetadata extension works the same way, with one image and no API contract:

1. In `extensions/openmetadata`, update the version in `pyproject.toml`, `uv.lock` (`uv lock`), `VERSION` in `openmetadata_sync/config.py` and the image tag in `compose.yaml`. Date its `CHANGELOG.md` section, and update the version in the install commands of `docs/openmetadata.md` and the extension's README. `uv run python -m scripts.release` rejects a mismatch.
2. After the merge into `main`, tag that commit right away, because the published documentation already names the new installer:

   ```bash
   git tag -a openmetadata-vX.Y.Z -m "OpenMetadata X.Y.Z"
   git push origin openmetadata-vX.Y.Z
   ```

The **OpenMetadata extension release** workflow runs its CI, including the end-to-end test against a local OpenMetadata server, builds `ghcr.io/sanderdw/iceberg-openmetadata` for AMD64 and ARM64, checks that it is public and publishes the release with its source archive, its installation bundle (`iceberg-openmetadata-X.Y.Z-install.tar.gz`: `compose.yaml` on the published image, the demo's `compose.openmetadata.yaml`, `scripts/setup.py` and `scripts/om_token.py`), `install.sh` and `SHA256SUMS`. `install.sh` downloads from its own release, never from `releases/latest`, which stays the platform's. On the first publication, set the package to **Public** and rerun the failed job. Its branch previews are named `openmetadata-SLUG-RUN_ID-ATTEMPT` and carry an installer of their own. To build them locally: `uv run --no-project python -m scripts.release --build --install` in `extensions/openmetadata`.


## Package visibility

On the first publication, open each package in [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and set it to **Public**, including `iceberg-conversationalbi`, `iceberg-conversationalbi-runtime`, `iceberg-dbt`, `iceberg-dbt-runner`, `iceberg-ingestion`, `iceberg-ingestion-runner` and `iceberg-openmetadata`, and the charts `charts/iceberg-data-platform`, `charts/iceberg-conversationalbi`, `charts/iceberg-dbt` and `charts/iceberg-ingestion`. The workflow checks for anonymous pull access before publishing a preview. Publishing uses the workflow's `GITHUB_TOKEN`, so no registry secret is needed.
