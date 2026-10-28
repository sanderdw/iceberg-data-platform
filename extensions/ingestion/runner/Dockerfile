# The isolated run image. Built with context extensions/ingestion. Each run reaches only the
# platform's catalog and storage on an internal network, and its source through an egress network.
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.14.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/runner
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/runner/.venv PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --only-group runner --no-install-project
COPY runner ./runner
RUN useradd --uid 10001 --no-create-home runner
ENV PATH=/opt/runner/.venv/bin:$PATH PYTHONPATH=/opt/runner HOME=/tmp/home XDG_CACHE_HOME=/tmp/cache \
    RUNTIME__DLTHUB_TELEMETRY=false DO_NOT_TRACK=1
RUN python -c "import dlt, pyiceberg, pg8000, pymysql, s3fs, runner.entrypoint, runner.sources"
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/runner/.venv/bin/python", "-m", "runner.entrypoint"]
