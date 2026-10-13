# The Ingestion extension's service image: the API/MCP/page (ing-gateway) and the launcher. Context: extensions/ingestion.
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15.0rc3-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group launcher --no-install-project
COPY ingestion ./ingestion
COPY launcher ./launcher
COPY contracts ./contracts
# The launcher (root, no capabilities) writes run artifacts; the gateway (10001) only reads them.
RUN useradd --uid 10001 --create-home ingestion && mkdir -p /data/artifacts /artifacts && chown 10001:10001 /data
USER 10001:10001
ENV ING_STATE_DIR=/data PORT=3008 HOST=0.0.0.0
EXPOSE 3008
CMD ["/app/.venv/bin/python", "-m", "ingestion"]
