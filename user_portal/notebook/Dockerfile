FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md ./
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group notebook
# CPU quotas do not constrain host CPU discovery in native numerical libraries.
# Bound each kernel's pools so several open notebooks fit the runtime's PID limit.
ENV OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2 ARROW_IO_THREADS=2
ENV DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
COPY user_portal/duckdb_extensions.py ./user_portal/
RUN /app/.venv/bin/python user_portal/duckdb_extensions.py
COPY user_portal/__init__.py ./user_portal/
COPY user_portal/notebook ./user_portal/notebook
RUN useradd --uid 10001 --create-home notebook && mkdir /work && touch /work/.keep && chown -R 10001:10001 /work
USER 10001:10001
WORKDIR /work
EXPOSE 2718
CMD ["/app/.venv/bin/python", "-m", "user_portal.notebook.start"]
