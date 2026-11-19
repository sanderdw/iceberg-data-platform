# The Replication extension's service image: the API/MCP/page (rep-gateway) and the launcher. Context: extensions/replication.
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.14.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group launcher --group otel --no-install-project
COPY replication ./replication
COPY launcher ./launcher
COPY contracts ./contracts
# The launcher (root, no capabilities) writes run artifacts; the gateway (10001) only reads them.
RUN useradd --uid 10001 --create-home replication && mkdir -p /data/artifacts /artifacts && chown 10001:10001 /data
USER 10001:10001
ENV REP_STATE_DIR=/data PORT=3014 HOST=0.0.0.0
EXPOSE 3014
CMD ["/app/.venv/bin/python", "-m", "replication"]
