FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.14.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md ./
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group notebook
# CPU quotas do not constrain host CPU discovery in native numerical libraries.
# Bound each kernel's pools so several open notebooks fit the runtime's PID limit.
# Polars' own jemalloc would add background purge threads; it purges inline instead.
ENV OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2 ARROW_IO_THREADS=2 \
    POLARS_MAX_THREADS=2 POLARS_ASYNC_THREAD_COUNT=2 _RJEM_MALLOC_CONF=background_thread:false
ENV DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
# dlt (example 08) would otherwise send anonymous usage data to dltHub.
ENV RUNTIME__DLTHUB_TELEMETRY=false
COPY user_portal/duckdb_extensions.py ./user_portal/
RUN /app/.venv/bin/python user_portal/duckdb_extensions.py
COPY user_portal/__init__.py ./user_portal/
COPY user_portal/notebook ./user_portal/notebook
RUN useradd --uid 10001 --create-home notebook && mkdir /work && touch /work/.keep && chown -R 10001:10001 /work
USER 10001:10001
WORKDIR /work
EXPOSE 2718
CMD ["/app/.venv/bin/python", "-m", "user_portal.notebook.start"]
