# The Evaluations extension's image: the API, MCP server, page, scheduler and runs. Context: extensions/evaluations.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.14.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group otel --no-install-project
COPY evaluations ./evaluations
COPY examples ./examples
COPY contracts ./contracts
RUN useradd --uid 10001 --create-home evaluations && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
ENV EVAL_STATE_DIR=/data PORT=3012 HOST=0.0.0.0
EXPOSE 3012
CMD ["/app/.venv/bin/python", "-m", "evaluations"]
