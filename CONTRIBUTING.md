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

Start both stacks as described in [Run from source](README.md#run-from-source). For live reload of the admin backend, stop its container and run it locally:

```bash
docker compose stop portal
uv run python -m server --reload
# afterwards: docker compose up -d --wait portal
```

The user portal works the same way: stop `users` and run `uv run --group users python -m user_portal`. See [architecture](docs/architecture.md) for the authorization boundaries.

## Tests

```bash
npm run verify           # unit and authorization tests, Ruff, JavaScript syntax
npm run check:release    # release allowlist, versions, licenses and Markdown links
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
```

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

With the [demo fixtures](docs/keycloak.md#demo-fixtures) installed:

```bash
npm run test:keycloak
npm run test:mcp
npm run test:mcp-admin
```

## Pull requests

- Keep pull requests focused, and describe the behaviour before and after the change plus the checks you ran.
- Add regression tests for behaviour changes, especially around authorization, grants, cleanup and data operations.
- Never include credentials, `.env` files, notebook output or screenshots that show secrets.
- Never delete volumes wholesale on a shared development environment.
- Update `uv.lock` or `package-lock.json` together with their manifests, and format Python with Ruff.
- Example notebooks run from top to bottom and must never overwrite existing user notebooks.

By submitting a contribution, you agree to license it under this project's Apache-2.0 license. Be respectful and keep discussions about the project.
