FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md ./
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group users --group oidc
# Table previews read Iceberg with DuckDB; they never download extensions at runtime.
ENV DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
COPY user_portal/duckdb_extensions.py ./user_portal/
RUN /app/.venv/bin/python user_portal/duckdb_extensions.py
COPY server/__init__.py server/models.py server/polaris.py server/oidc.py server/entrypoints.py server/validation.py ./server/
COPY user_portal/__init__.py user_portal/app.py user_portal/directory.py user_portal/runtime.py user_portal/__main__.py ./user_portal/
COPY user_portal/catalog.py user_portal/preview.py ./user_portal/
COPY user_portal/public ./user_portal/public
COPY public/fonts ./user_portal/public/fonts
COPY public/favicon.svg ./user_portal/public/favicon.svg
EXPOSE 3002
CMD ["uv", "run", "--no-sync", "python", "-m", "user_portal"]
