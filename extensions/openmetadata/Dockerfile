# The OpenMetadata extension's service image (om-sync). Context: extensions/openmetadata.
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.14.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY openmetadata_sync ./openmetadata_sync
RUN useradd --uid 10001 --create-home openmetadata
USER 10001:10001
ENV PORT=3011 HOST=0.0.0.0
EXPOSE 3011
CMD ["/app/.venv/bin/python", "-m", "openmetadata_sync"]
