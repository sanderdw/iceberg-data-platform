# The isolated run image. Built with context stacks/dbt; it has no network at run time.
FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.14.7-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/runner
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/runner/.venv PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
# dbt-oss verifies the sha256 of the platform wheel it installs (see its sdist's assets.json).
RUN uv sync --locked --no-dev --only-group runner --no-install-project
ENV HOME=/opt/runner/home ADBC_DRIVER_PATH=/opt/runner/adbc PYTHONPATH=/opt/runner
COPY runner ./runner
RUN /opt/runner/.venv/bin/python -m runner.install && /opt/runner/.venv/bin/dbt --version
RUN useradd --uid 10001 --no-create-home runner
ENV PATH=/opt/runner/.venv/bin:$PATH XDG_CACHE_HOME=/tmp/cache DBT_SEND_ANONYMOUS_USAGE_STATS=false DO_NOT_TRACK=1
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/runner/.venv/bin/python", "-m", "runner.entrypoint"]
