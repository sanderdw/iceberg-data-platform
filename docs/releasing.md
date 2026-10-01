# Publishing a release

The `Release` workflow publishes three container images (`ghcr.io/sanderdw/iceberg-data-platform-{portal,users,notebook}`) and the source and installation archives. Local tooling only prepares artifacts.

| Channel | Published by | Release | Image tags | Installer |
| --- | --- | --- | --- | --- |
| Stable | `vX.Y.Z` tag on `main` | `vX.Y.Z`, marked Latest if highest | `:X.Y.Z`, `:latest` | `/releases/latest/download/install.sh` or `/releases/download/vX.Y.Z/install.sh` |
| Branch preview | Push to a listed branch, or **Run workflow** | Prerelease `SLUG-RUN_ID-ATTEMPT` plus rolling `SLUG-preview` | `:SLUG-RUN_ID-ATTEMPT` | `/releases/download/SLUG-preview/install.sh` |

Every installer is pinned to its own release and image tag. Previews never become Latest and never write `:latest`.

## Publish a branch for testers

Branches listed under `on.push.branches` in `.github/workflows/release.yml` publish on every push. Any other branch publishes on demand, either through **Actions → Release → Run workflow** or with `gh workflow run Release --ref BRANCH`.

The workflow runs the full CI suite and builds AMD64 and ARM64 images. Only then does it publish the exact prerelease and update the `SLUG-preview` installers. A failed run leaves the previous preview in place.

`SLUG` is the branch name in lowercase, with characters other than letters, digits, `.`, `_` and `-` replaced by `-`, up to 60 characters. Branch names that start with `v` plus a digit are rejected. The workflow keeps the five newest builds per branch. Delete the `SLUG-preview` release after merging.

To build a pinned bundle locally without publishing:

```bash
uv run python -m scripts.release --build --install --release-tag BRANCH-123-1 --image-tag BRANCH-123-1
```

## Publish a stable release

1. Update the version in `pyproject.toml`, `package.json`, both fields in `package-lock.json`, `uv.lock` (`uv lock`) and the API versions in `server/app.py` and `user_portal/app.py`. `npm run check:release` rejects any mismatch. Update `CHANGELOG.md`, add the release notes in `docs/releases/X.Y.Z.md` and update the version in the install examples. Record Bridge contract changes in `contracts/bridge/CHANGELOG.md` and the version in `server/bridge.py` (see [COMPATIBILITY.md](../contracts/bridge/COMPATIBILITY.md)); the Bridge has no release of its own.
2. Run the [checks](../CONTRIBUTING.md#tests) and the dependency audits:

   ```bash
   uv export --locked --all-groups --no-hashes --no-emit-project --format requirements-txt -o /tmp/iceberg-requirements.txt
   uvx pip-audit==2.10.1 -r /tmp/iceberg-requirements.txt --no-deps --disable-pip
   npm audit
   ```

3. Merge into `main`, wait for CI, then [release Conversational BI](#release-an-extension) if its version changed. The installation bundle installs Conversational BI from its own release, so a stable core release requires the tag `conversationalbi-v<version>` of the version in `extensions/conversationalbi/pyproject.toml`, unchanged `compose.yaml` and `scripts/setup.py` in that folder since the tag, and public images. A branch preview only warns.
4. Tag the core commit on `main`:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

The workflow rejects a tag that doesn't match the project version or isn't on `main`. It then:

1. runs the checks and a Gitleaks scan of the source archive
2. builds the images on native AMD64 and ARM64
3. publishes the GitHub release with the archives and `SHA256SUMS`
4. confirms that the `latest` installer URLs serve the new version

To check a release before merging, run **Actions → Release candidate → Run workflow**. It runs `npm run verify`, builds the source archive, scans it with Gitleaks and publishes nothing.

Never move a published tag. Fix the cause and rerun the workflow instead.

To inspect the artifacts locally, run `uv run python -m scripts.release --build --install --release-tag vX.Y.Z --image-tag X.Y.Z`. This writes a deterministic source archive, an install bundle and checksums to `dist/`. `check:release` enforces a source allowlist, matching versions, license notices and valid local Markdown links. It also rejects known local secrets.

## Release an extension

Extensions are versioned and released on their own, with tags `<id>-vX.Y.Z` that never mark the latest release. For Conversational BI:

1. In `extensions/conversationalbi`, update the version in `pyproject.toml`, `uv.lock` (`uv lock`), `conversationalbi/config.py`, `web/package.json` and `runtime/package.json` with their lock files, the two image tags in `compose.yaml` and the API contract (`uv run python -m scripts.api_contract`). Date its `CHANGELOG.md` section. `uv run python -m scripts.release` rejects a mismatch.
2. After the merge into `main`, tag that commit:

   ```bash
   git tag -a conversationalbi-vX.Y.Z -m "Conversational BI X.Y.Z"
   git push origin conversationalbi-vX.Y.Z
   ```

The **Conversational BI release** workflow runs its CI, builds `ghcr.io/sanderdw/iceberg-conversationalbi` and `iceberg-conversationalbi-runtime` for AMD64 and ARM64, checks that both are public and publishes the release with its source archive. On the first publication, set both packages to **Public** and rerun the failed job. Then release the core.

## Package visibility

On the first publication, open each package in [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and set it to **Public**. The workflow checks for anonymous pull access before publishing a preview. Publishing uses the workflow's `GITHUB_TOKEN`, so no registry secret is needed.
