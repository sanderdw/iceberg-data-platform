"""Disposable reporting containers; only temporary viewer credentials enter them."""

import json
import threading
import time
from contextlib import suppress
from uuid import uuid4

import docker
from docker.errors import NotFound
from docker.types import LogConfig

from server.models import ServiceError

LABEL = "iceberg.users.reporting"


class ReportRuntime:
    def __init__(self, env):
        self.docker = docker.from_env(timeout=10)
        self.scope = env.get("USER_STACK_NAME", "iceberg-workspaces")
        self.image = env.get("REPORTING_IMAGE", "iceberg-user-reporting:0.1.0")
        self.polaris = env.get("POLARIS_CONTAINER", "iceberg-platform-polaris-1")
        self.rustfs = env.get("RUSTFS_CONTAINER", "iceberg-platform-rustfs-1")

    def remove_network(self, network):
        with suppress(NotFound):
            network.reload()
            for identifier in network.attrs.get("Containers", {}):
                with suppress(NotFound):
                    network.disconnect(identifier, force=True)
            network.remove()

    def recover(self):
        filters = {"label": f"{LABEL}={self.scope}"}
        for container in self.docker.containers.list(all=True, filters=filters):
            container.remove(force=True)
        for network in self.docker.networks.list(filters=filters):
            self.remove_network(network)

    def run(self, request, cancelled: threading.Event):
        if cancelled.is_set():
            raise ServiceError(409, "Report cancelled.")
        identifier = uuid4().hex
        labels = {LABEL: self.scope}
        network = self.docker.networks.create(
            f"{self.scope}-report-{identifier}", internal=True, labels=labels
        )
        container = None
        try:
            network.connect(self.polaris, aliases=["polaris"])
            network.connect(self.rustfs, aliases=["rustfs"])
            source = {
                **request["source"],
                "uri": "http://polaris:8181/api/catalog",
                "s3Endpoint": "http://rustfs:9000",
            }
            container = self.docker.containers.run(
                self.image,
                detach=True,
                name=f"{self.scope}-report-{identifier}",
                labels=labels,
                network=network.name,
                user="10001:10001",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="1g",
                nano_cpus=2_000_000_000,
                pids_limit=128,
                tmpfs={"/tmp": "rw,nosuid,nodev,size=128m,mode=1777"},
                log_config=LogConfig(type="json-file", config={"max-size": "5m", "max-file": "1"}),
                environment={"REPORT_REQUEST": json.dumps({**request, "source": source}, allow_nan=False)},
            )
            deadline = time.monotonic() + 45
            while True:
                if cancelled.is_set():
                    raise ServiceError(409, "Report cancelled.")
                if time.monotonic() >= deadline:
                    raise ServiceError(504, "Report exceeded 45 seconds. Add a filter or simplify the query.")
                container.reload()
                if container.status in ("exited", "dead"):
                    break
                cancelled.wait(0.2)
            output = container.logs(stdout=True, stderr=False)
            if container.attrs["State"]["ExitCode"] or len(output) > 4_000_000:
                raise ServiceError(
                    422, "Report unavailable. Check the columns, chart axes, SQL and data permissions."
                )
            return json.loads(output)
        finally:
            if container is not None:
                with suppress(NotFound):
                    container.remove(force=True)
            self.remove_network(network)

    def close(self):
        self.docker.close()
