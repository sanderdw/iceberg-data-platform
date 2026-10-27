# Contributing

Bug reports, documentation improvements and focused pull requests are welcome. Use English for all interface text, messages, documentation, comments and tests.

## Development

Install Docker with Compose v2, [uv](https://docs.astral.sh/uv/) and Node.js 24 or later, then:

```bash
uv python install
uv sync --locked --all-groups
npm ci
uv run python -m scripts.setup
```

Start both stacks as described in [run from source](run-from-source.md). For live reload of the admin backend, stop its container and run it locally:

```bash
docker compose stop portal
uv run python -m server --reload
# afterwards: docker compose up -d --wait portal
```

The user portal works the same way: stop `users` and run `uv run --group users python -m user_portal`. See [architecture](architecture.md) for the authorization boundaries.

## Tests

```bash
npm run verify           # unit and authorization tests, Ruff, JavaScript syntax
npm run check:release    # release allowlist, versions, licenses and Markdown links
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
```

`test/test_helm_chart.py` renders the Helm chart and is skipped without `helm`. Install the pinned
tools with `sh dev/kubernetes/tools.sh .local/bin` to run it, and `sh dev/kubernetes/kind_check.sh`
(add `POLARIS=official` for the official Polaris chart) to install the chart on kind and run the
smoke tests against it, as CI does; see [Kubernetes](kubernetes.md).

Browser checks run against fixtures and don't need a running stack:

```bash
npx playwright install chromium
npm run test:team-ui
npm run test:catalog
npm run test:shares-ui
npm run test:forms-ui
npm run test:navigation-ui
```

With both stacks running (each test creates and cleans up its own resources):

```bash
UV_NO_SYNC=1 npm run test:e2e
uv run --all-groups python -m scripts.smoke
uv run --all-groups python -m scripts.data_smoke
uv run --all-groups python -m scripts.catalog_smoke
uv run --all-groups python -m scripts.share_smoke
uv run --all-groups python -m scripts.duckdb_smoke
npm run test:users
npm run test:examples
```

With the [demo fixtures](keycloak.md#demo-fixtures) installed:

```bash
npm run test:keycloak
npm run test:mcp
npm run test:mcp-admin
npm run test:connect
```

`scripts.bridge_smoke` checks the Extension Bridge with a registered extension; see its docstring. Each extension in `extensions/` has its own tests and CI; see [Conversational BI](conversational-bi.md#development), [dbt](dbt.md#development) and [Ingestion](ingestion.md#development).

## Documentation

This site is built with [Zensical](https://zensical.org) from `docs/` and `zensical.toml`. Preview it while you write, and build it as the `Pages` workflow does:

```bash
npm run docs:serve    # http://localhost:8000, rebuilds on save
npm run docs:build    # strict: fails on broken links and anchors
```

Pages for people who run the installer go in `docs/`; pages about building and internals go in `docs/develop/`. Add every new page to `nav` in `zensical.toml`. Link to files outside `docs/` with a full GitHub URL.

The site follows [Diátaxis](https://diataxis.fr): each page serves one need, and the nav groups pages by it.

| Kind | Reader | Write |
| --- | --- | --- |
| Tutorial | is learning | One path that always works, with the result of every step. No choices and no detours |
| How-to guide | has a task | Steps toward one goal, titled with a verb. Assume the reader knows the platform |
| Reference | looks something up | Tables and facts, structured like the product. No advice |
| Concept | wants to understand | The why, the trade-offs and the context. No steps |

Extension pages keep one order: an introduction with a table of addresses, **Install**, **Connect an AI agent** and other tasks, then how it works, then the reference sections such as settings and limits, and a link to its internals. Keep role tables in [roles and permissions](../reference/roles.md), not on the page.

Published URLs are linked from the portals, the installer and the skills, so never rename a page or a heading that has inbound links. Move a page in `nav` instead, and when you move content to another page, leave the heading behind with a link.

## Pull requests

- Keep pull requests focused, and describe the behaviour before and after the change plus the checks you ran.
- Check the change against the [five principles](index.md#principles): clean UX, API first, AI native, open source and loosely coupled. A new feature has a documented API route and, where agents need it, an MCP tool.
- Add regression tests for behaviour changes, especially around authorization, grants, cleanup and data operations.
- Never include credentials, `.env` files, notebook output or screenshots that show secrets.
- Never delete volumes wholesale on a shared development environment.
- Update `uv.lock` or `package-lock.json` together with their manifests, and format Python with Ruff.
- Example notebooks run from top to bottom and must never overwrite existing user notebooks.

By submitting a contribution, you agree to license it under this project's Apache-2.0 license. Be respectful and keep discussions about the project.
