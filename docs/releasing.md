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

1. Update the version in `pyproject.toml`, `package.json`, both fields in `package-lock.json`, `uv.lock` (`uv lock`) and the API versions in `server/app.py` and `user_portal/app.py`. `npm run check:release` rejects any mismatch. Update `CHANGELOG.md` and the version in the install examples.
2. Run the [checks](../CONTRIBUTING.md#tests) and the dependency audits:

   ```bash
   uv export --locked --all-groups --no-hashes --no-emit-project --format requirements-txt -o /tmp/iceberg-requirements.txt
   uvx pip-audit==2.10.1 -r /tmp/iceberg-requirements.txt --no-deps --disable-pip
   npm audit
   ```

3. Merge into `main`, wait for CI, then tag that commit:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

The workflow rejects a tag that doesn't match the project version or isn't on `main`. It then:

1. runs the checks and a Gitleaks scan of the source archive
2. builds the images on native AMD64 and ARM64
3. publishes the GitHub release with the archives and `SHA256SUMS`
4. confirms that the `latest` installer URLs serve the new version

Never move a published tag. Fix the cause and rerun the workflow instead.

To inspect the artifacts locally, run `uv run python -m scripts.release --build --install --release-tag vX.Y.Z --image-tag X.Y.Z`. This writes a deterministic source archive, an install bundle and checksums to `dist/`. `check:release` enforces a source allowlist, matching versions, license notices and valid local Markdown links. It also rejects known local secrets.

## Package visibility

On the first publication, open each package in [GitHub Packages](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and set it to **Public**. The workflow checks for anonymous pull access before publishing a preview. Publishing uses the workflow's `GITHUB_TOKEN`, so no registry secret is needed.
