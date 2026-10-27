# Run from source

Requires Docker with Compose v2, [uv](https://docs.astral.sh/uv/getting-started/installation/) and at least 4 GiB of Docker memory.

```bash
uv python install
uv sync --locked --all-groups
uv run python -m scripts.setup      # generates .env with random credentials; never commit it
docker compose up -d --build --wait
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users
```

Rerun the three Docker commands after source changes. Save your notebook work first: recreating the user portal stops running notebooks.

For changes to the user portal only, run `docker compose -f compose.users.yaml up -d --build --wait users`. This skips rebuilding the notebook image.

The services and their addresses are listed in [services](../reference/services.md). The platform also serves the **Extension Bridge** at `http://localhost:3005/bridge/v1`, which accepts only tokens of registered extensions; see the [Extension Bridge](extension-bridge.md).

Each extension is built and started from its own folder; see [Conversational BI](conversational-bi.md#install-from-source), [dbt](dbt.md#install-from-source) and [Ingestion](ingestion.md#install-from-source).

To run the platform from source on a local Kubernetes cluster instead, see [test on kind](kubernetes.md#test-on-kind).
