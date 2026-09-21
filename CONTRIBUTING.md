# Contributing

Bug reports, documentation improvements and focused pull requests are welcome. Use English for all interface text, error messages, documentation, examples, comments and tests.

## Development

Install Docker Compose, uv and Node.js 24 or later. Use the pinned Python version:

```bash
uv python install
uv sync --locked --all-groups
npm ci
uv run python -m scripts.setup
npm run verify
npm run check:release
```

For backend development, start the platform, including Keycloak provisioning, then
stop the administration container before running FastAPI locally on the same port:

```bash
docker compose up -d --build --wait
docker compose stop portal
uv run python -m server --reload
```

See [development instructions](README.md#development-and-tests) for restoring the container and local monitoring, [Run from source](README.md#run-from-source) for the standalone user stack, and [architecture](docs/architecture.md) for authorization boundaries.

## Changes and validation

Keep pull requests focused on a concrete problem. Describe the behavior before and after the change and the checks you ran. Add regression tests for behavior changes, especially authorization, grants, cleanup and data operations. Never include actual credentials, copied `.env` files, personal notebook output or screenshots containing secrets.

```bash
npm run verify
uv run --all-groups marimo check user_portal/notebook/template.py user_portal/notebook/examples/*.py
# Browser fixtures; no running stack needed:
npx playwright install chromium
npm run test:team-ui
npm run test:catalog
npm run test:shares-ui
npm run test:forms-ui
npm run test:navigation-ui
# With both Compose stacks running:
npm run test:e2e
npm run test:examples
```

Integration tests create uniquely named resources and clean up only their own resources. Never run a blanket volume deletion against a shared development environment.

Update `uv.lock` or `package-lock.json` together with dependency manifests. Use `uv` for Python dependency management. Format Python changes with Ruff. Follow marimo's reactive cell model for example notebooks; put writes behind an explicit run button, unless the example is a linear notebook that only recreates its own table and preserve existing user notebooks when seeding examples.

By submitting a contribution, you agree to license it under this project's Apache-2.0 license. Do not submit material you do not have permission to contribute. Be respectful, discuss ideas rather than people, and keep reports and reviews relevant to the project.
