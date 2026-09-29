# The dbt stack's service image: the API/MCP/viewer (dbt-portal) and the launcher. Context: stacks/dbt.
FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --group launcher --no-install-project
COPY dbt_portal ./dbt_portal
COPY launcher ./launcher
COPY platform_macros ./platform_macros
COPY templates ./templates
COPY contracts ./contracts
# The launcher (root, no capabilities) writes run artifacts; the portal (10001) only reads them.
RUN useradd --uid 10001 --create-home dbt && mkdir -p /data/artifacts /artifacts && chown 10001:10001 /data
USER 10001:10001
ENV DBT_STATE_DIR=/data PORT=3004 HOST=0.0.0.0
EXPOSE 3004
CMD ["/app/.venv/bin/python", "-m", "dbt_portal"]
