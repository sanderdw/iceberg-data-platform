FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md ./
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group notebook
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN /app/.venv/bin/python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/tmp/playwright
ENV DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
RUN /app/.venv/bin/python -c "import duckdb; c = duckdb.connect(config={'extension_directory': '/opt/duckdb/extensions'}); c.execute('INSTALL httpfs; INSTALL iceberg; LOAD httpfs; LOAD iceberg'); c.close()"
COPY user_portal/__init__.py ./user_portal/
COPY user_portal/notebook ./user_portal/notebook
RUN useradd --uid 10001 --create-home notebook && mkdir /work && touch /work/.keep && chown -R 10001:10001 /work
USER 10001:10001
WORKDIR /work
EXPOSE 2718
CMD ["/app/.venv/bin/python", "-m", "user_portal.notebook.start"]
