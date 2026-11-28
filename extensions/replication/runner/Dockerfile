# The isolated run image for PostgreSQL sources, built on OLake's own image (Apache-2.0) so Dependabot
# proposes OLake updates. Built with context extensions/replication. Each run reaches only the
# platform's catalog and storage on an internal network, and its source through an egress network.
# The start-up program (runner/) uses Python's standard library only.
FROM ghcr.io/astral-sh/uv:0.13.10 AS uv
FROM olakego/source-postgres:v1.2.0
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
RUN uv python install 3.14 && ln -s "$(uv python find 3.14)" /usr/local/bin/python3 && rm /usr/local/bin/uv \
    && useradd --uid 10001 --no-create-home runner
WORKDIR /opt/runner
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY runner ./runner
ENV PYTHONPATH=/opt/runner PYTHONDONTWRITEBYTECODE=1 HOME=/tmp TELEMETRY_DISABLED=true OLAKE_BIN=/home/olake
RUN python3 -c "import runner.entrypoint, runner.remote" && /home/olake --help >/dev/null
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python3", "-m", "runner.entrypoint"]
