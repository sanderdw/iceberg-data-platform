# Working on the dbt stack

- Remove the example pipeline (regions) after a created dbt project
- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, images and
  version. Run everything from `stacks/dbt`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`dbt_portal/bridge_client.py`) and standard Iceberg REST/S3.
- Every capability is a service method in `dbt_portal/services.py`, exposed as a REST operation
  (`dbt_portal/api.py`, listed in `OPERATIONS`) and an MCP tool (`dbt_portal/mcp_server.py`).
  `tests/test_api.py` fails when they diverge or when `contracts/dbt-api/v1/openapi.yaml` is stale:
  regenerate it with `uv run python -m scripts.api_contract`.
- Authorization lives in `Services`; runs only get tokens from the Bridge, never secrets.
- The launcher is the only component with the Docker socket: keep its inputs fixed (image, flags,
  mounts). Runner code (`runner/`) runs inside the isolated image without network.
- `scripts/e2e_smoke.py` checks a live installation through MCP.
- When you need to make a v3 version of a Iceberg table you can use following example:
  lakehouse.execute(f"""
      CREATE TABLE {selected_table} (
          column1 BIGINT,
          column2 VARCHAR,
          column3 VARCHAR,
          column4 GEOMETRY,
          column5 TIMESTAMP_NS,
          column6 VARIANT,
          column7 BOOLEAN
     ) WITH ('format-version' = 3, '{EXAMPLE_PROPERTY}' = '{EXAMPLE}')
  """)
