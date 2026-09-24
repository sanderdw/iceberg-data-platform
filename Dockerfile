FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md ./
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group oidc --group mcp
COPY server ./server
COPY scripts/setup.py ./scripts/setup.py
COPY public ./public
RUN useradd --uid 10001 --create-home portal
USER portal
ENV HOST=0.0.0.0 PORT=3000
EXPOSE 3000
CMD ["uv", "run", "--no-sync", "python", "-m", "server"]
