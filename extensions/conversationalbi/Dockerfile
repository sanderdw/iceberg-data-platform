# Conversational BI gateway and agent (one process, ports 3007 and 3017). Context: extensions/conversationalbi.
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# No install scripts: nothing phones home at build time.
RUN SCARF_ANALYTICS=false npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN mkdir -p /conversationalbi && npx vite build --outDir /conversationalbi/public

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY conversationalbi ./conversationalbi
RUN /app/.venv/bin/python -m conversationalbi.duckdb_extensions
COPY contracts ./contracts
COPY --from=web /conversationalbi/public ./conversationalbi/public
RUN useradd --uid 10001 --no-create-home cbi
USER 10001:10001
ENV PORT=3007 AGENT_PORT=3017 HOST=0.0.0.0 PYDANTIC_AI_NO_BANNER=1 DO_NOT_TRACK=1 HOME=/tmp
EXPOSE 3007
CMD ["/app/.venv/bin/python", "-m", "conversationalbi"]
