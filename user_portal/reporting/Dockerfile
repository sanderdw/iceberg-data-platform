FROM ghcr.io/astral-sh/uv:0.12.13 AS uv
FROM python:3.13.14-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 \
    DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false DO_NOT_TRACK=1
COPY user_portal/reporting/pyproject.toml user_portal/reporting/uv.lock ./
RUN uv sync --locked --no-dev
COPY user_portal/duckdb_extensions.py ./user_portal/
RUN /app/.venv/bin/python user_portal/duckdb_extensions.py
COPY server/__init__.py server/models.py ./server/
COPY user_portal/__init__.py user_portal/preview.py ./user_portal/
COPY user_portal/reporting/__init__.py user_portal/reporting/query.py user_portal/reporting/worker.py ./user_portal/reporting/
COPY user_portal/reporting/definitions.py user_portal/reporting/execution.py ./user_portal/reporting/
USER 10001:10001
ENV HOME=/tmp
ENTRYPOINT ["/app/.venv/bin/python", "-m", "user_portal.reporting.worker"]
