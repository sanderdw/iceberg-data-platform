---
name: release
description: Release a new version of the Iceberg Data Platform from start to finish on the user's behalf. Creates the X.Y.Z version branch, applies the open Dependabot updates, bumps the versions, updates the documentation with a ROT check and the Write the Docs principles, checks whether the presentation needs an update, runs the tests, merges and pushes the tags. Use when the user explicitly asks to release, cut or ship a version of the platform or its extensions.
---

# Release the Iceberg Data Platform

[`docs/develop/releasing.md`](../../../docs/develop/releasing.md) is the reference for which files carry a version, the tag names and what each workflow does. `.github/workflows/ci.yml` is the reference for the checks. Read both first. This skill adds the order, the decisions and the traps.

**You do the release.** Asking for this skill is the authorization to commit, push, open and merge the pull request, push tags and rerun workflows, all under the user's git identity. Don't hand commands back to the user. Add no `Co-Authored-By` line or any other AI attribution to commits, pull requests, tags or release notes.

**Stop and ask only for:**
- stopping a running development stack, or anything that touches `iceberg-platform_*` volumes. An installed platform shares that Compose project name, so `down --volumes` can destroy its data
- setting a newly published GHCR package to Public, which has no API. Give the [packages link](https://github.com/users/sanderdw/packages?repo_name=iceberg-data-platform) and the package names, then rerun the failed job yourself
- a failure whose fix would change what the product does

**Never** force-push, move a published tag, use `--no-verify` or merge with red checks. If a published tag turns out wrong, fix the cause and release the next patch.

Say plainly what you're doing at each step, and name every branch change before you make it.

## 1. Version branch

Use the version the user gave. Otherwise take the next patch after the latest stable tag and say so:

```bash
git fetch origin --tags
git tag --sort=-v:refname --list 'v[0-9]*' | head -1
```

If a branch named `X.Y.Z` is checked out, stay on it. Otherwise:

```bash
git switch -c X.Y.Z origin/main
```

The name is the bare version: the Release workflow rejects branch names that start with `v` plus a digit. If the working tree holds changes that don't belong in this release, ask what to do with them first.

## 2. Dependencies

```bash
gh pr list --author app/dependabot --state open --limit 200 --json number,title,headRefName,createdAt
```

Read the `ignore` rules in `.github/dependabot.yml`, then apply the updates on the release branch, in the directory the pull request names. Don't merge the pull requests one by one: Dependabot closes them once `main` has the version. Where several overlap, take the highest version.

| Ecosystem | How |
| --- | --- |
| uv | `uv lock --upgrade-package NAME` |
| npm | `npm install NAME@VERSION`, keeping the file's exact or range style |
| Docker | the `FROM` line |
| GitHub Actions | the commit SHA and its version comment, in every workflow |

- **Skip and report** anything an `ignore` rule excludes. Older pull requests can predate a rule, such as a new Python minor.
- **Take major versions.** This project keeps no backwards compatibility: fix the repository's own code and examples, and add no shims.
- **If an update can't land,** leave it out, report why and add an `ignore` rule with a comment giving the reason, like the CopilotKit one.
- **A new `uv` version** also changes the `version:` pins in the workflows.
- **Never run `npm audit fix` in `extensions/conversationalbi/runtime`.** It downgrades below the advisory range. Use `overrides` in `package.json`, then start the runtime to check it.

## 3. Versions

Bump the core as `releasing.md` lists it. For each extension (`conversationalbi`, `dbt`, `ingestion`, `evaluations`, `openmetadata`), read the version in its `pyproject.toml` and look for changes since its tag:

```bash
git diff --stat <id>-v<version> -- extensions/<id>
```

Any change that ships, lock files included, needs a new patch version. Changed bundled files without a new tag fail the core release gate.

Then look for hard-coded leftovers of each old version. The OpenMetadata tests have them, including an escaped regex and a deliberately different version in the "other images" test:

```bash
git grep -nF 'OLD_VERSION' -- ':!docs/releases' ':!docs/changelog.md' ':!*CHANGELOG.md' ':!*.lock' ':!*package-lock.json'
```

## 4. Documentation

The scope is everything that changed since the last tag: `git diff --stat vLAST`.

**Release files.** Update `docs/changelog.md`, add `docs/releases/X.Y.Z.md` modelled on the previous one, add it to `nav` in `zensical.toml`, date the extension changelogs and update the install examples. Use today's date. Treat every install as a clean setup: no upgrade or migration notes. Address the reader as "you" and name no maintainer.

**ROT check.** Go through `docs/`, the READMEs, `AGENTS.md` files and skills, starting with the pages near what changed, and search all of them for anything this release renamed or removed.

| | Look for | Do |
| --- | --- | --- |
| **Redundant** | The same content in two places | Keep one, link to it |
| **Outdated** | Versions, flags, environment variables, paths, links and behaviour that no longer match. Check them against the code, not from memory | Correct it, and remove legacy remnants |
| **Trivial** | Text that restates the interface, or that no reader needs | Delete it |

Older changelog entries and release notes are history: leave them.

**[Write the Docs principles](https://www.writethedocs.org/guide/writing/docs-principles).** Check each changed page:

| Principle | Check |
| --- | --- |
| ARID | Repetition only where a reader needs it on the spot |
| Skimmable | Descriptive headings and link text; the main point first |
| Exemplary | A working example for the most common use, kept apart from reference material |
| Consistent | The same term for the same thing, and the same formatting as nearby pages |
| Current | Nothing incorrect; wrong is worse than missing. Prefer wording that needs no version |
| Nearby | Sources live in this repository, next to what they describe |
| Unique | Each page has its own scope, with no parallel text to maintain |
| Discoverable | Linked from wherever a reader would look, and in `nav` |
| Addressable | A heading to link to for each topic |
| Cumulative | Prerequisites come before what depends on them |
| Complete | A topic is covered fully, or the page says what it leaves out |
| Beautiful | Renders cleanly: tables, code blocks, command output |
| Comprehensive | Every new capability answers what it does, how to use it and where it stops |

Then build the site, which also fails on broken links:

```bash
npm run docs:build
```

## 5. Presentation

Optional, and it never blocks a release. Check whether the release changes anything `presentation/index.html` states, such as a feature, a component version or the Bridge contract version. Correct small factual text in place. Don't add slides or capture screenshots: name what is worth doing in the final report.

## 6. Testing

Commit first, with the message `X.Y.Z`, because `npm run check:release` reads the git index. Then read `ci.yml` and run what it runs today:

- the `source` job
- the `source` job of each bumped extension's `<id>-ci.yml`
- the `integration` job in CI mode from a clean state, with its `uvicorn ... create_app --factory` command overrides

The integration job needs the development stack's ports. If a stack is running, ask once whether you may stop it, and restore it afterwards with plain `docker compose up -d --wait` for both compose files. If the answer is no, rely on the pull request's CI and report that the local run was skipped. Distrust state a clean runner lacks, such as `~/.duckdb/extensions` or cached images.

Then push and open the pull request:

```bash
git push -u origin X.Y.Z
gh pr create --base main --head X.Y.Z --title X.Y.Z --body-file NOTES
gh pr checks X.Y.Z --watch
```

Write `NOTES` from the release notes. This pull request's CI is the only place the `keycloak`, `kubernetes`, `notebook-image` and `installers` jobs run. When something goes red, fix the cause and push; rerun without a change only for a clear infrastructure failure.

## 7. Merge and tag

Name the switch to `main`, then:

```bash
gh pr merge X.Y.Z --squash
git switch main && git pull --ff-only
gh run list --branch main --limit 10
```

Wait for CI on `main`. Then tag in this order, only the extensions with a new version, and the core last. Evaluations needs the Conversational BI release that has its API, and the core gate needs every bundled extension's tag and public images.

1. `conversationalbi-vX.Y.Z`
2. `evaluations-vX.Y.Z`
3. `dbt-vX.Y.Z`
4. `ingestion-vX.Y.Z`
5. `openmetadata-vX.Y.Z`
6. `vX.Y.Z`

Use the annotated tag messages from `releasing.md`, and **one `git push` per tag**: GitHub fires no events when more than three tags arrive at once, so no workflow starts. Watch each workflow to green before the next tag:

```bash
git tag -a <tag> -m "<message>"
git push origin <tag>
gh run list --branch <tag> --limit 1 --json databaseId,workflowName,status
gh run watch <id> --exit-status
```

If a first publication fails because a package isn't public, ask the user to set it, then `gh run rerun <id> --failed`. Before tagging the core, confirm each bundled extension's tag exists for the version in its `pyproject.toml`.

## 8. Finish

```bash
gh release view vX.Y.Z
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | grep -F X.Y.Z
gh release view X.Y.Z-preview && gh release delete X.Y.Z-preview --cleanup-tag --yes
```

The last line runs only if a branch preview exists. Report:

- each version released, with its release link
- Dependabot updates applied, and those skipped with the reason
- what the ROT check changed or removed
- the presentation verdict
- anything not run or not verified
