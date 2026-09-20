"""Launch isolated marimo editors. No notebook gets Docker or platform credentials."""

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from hashlib import sha256

import docker
import httpx
from docker.errors import APIError, NotFound
from docker.types import LogConfig

from server.models import ServiceError

LABEL = "iceberg.users.runtime"


def filespace_key(team, environment):
    return sha256(json.dumps([team, environment]).encode()).hexdigest()[:32]


@dataclass
class Workspace:
    id: str
    session_id: str
    user_id: str
    team: str
    database: str
    key: str
    container: object = field(repr=False)
    network: object = field(repr=False)
    upstream: str
    token: str = field(repr=False)
    environment: str = "development"

    @property
    def url(self):
        return f"/workspaces/{self.id}/"

    def public(self):
        return {
            "id": self.id,
            "database": self.database,
            "team": self.team,
            "environment": self.environment,
            "filespace": self.key,
            "url": self.url + "?file=workspace.py",
            "filesUrl": self.url,
            "examples": [
                {
                    "title": "01 · Neighborhood data with PyIceberg",
                    "url": self.url + "?file=01_pyiceberg_neighborhood.py",
                },
                {"title": "02 · Visualize with DuckDB", "url": self.url + "?file=02_duckdb_visualization.py"},
                {"title": "03 · Native DuckDB on Iceberg", "url": self.url + "?file=03_duckdb_iceberg.py"},
                {
                    "title": "04 · Write Iceberg v3 with DuckDB",
                    "url": self.url + "?file=04_duckdb_iceberg_v3_write.py",
                },
                {
                    "title": "05 · Read Iceberg v3 with DuckDB",
                    "url": self.url + "?file=05_duckdb_iceberg_v3_read.py",
                },
            ],
        }


class NotebookRuntime:
    def __init__(self, env):
        self.docker = docker.from_env(timeout=30)
        self.scope = env.get("USER_STACK_NAME", "iceberg-workspaces")
        self.image = env.get("NOTEBOOK_IMAGE", "iceberg-user-notebook:0.1.0")
        self.gateway = env.get("USER_PORTAL_CONTAINER", os.environ.get("HOSTNAME", ""))
        self.polaris = env.get("POLARIS_CONTAINER", "iceberg-platform-polaris-1")
        self.rustfs = env.get("RUSTFS_CONTAINER", "iceberg-platform-rustfs-1")
        self.limit = int(env.get("MAX_NOTEBOOKS", "8"))
        self.memory = env.get("NOTEBOOK_MEMORY", "1g")
        self.workspaces = {}

    def cleanup_network(self, network):
        network.reload()
        for container_id in network.attrs.get("Containers", {}):
            try:
                network.disconnect(container_id, force=True)
            except NotFound:
                pass
        network.remove()

    def recover(self):
        # Sessions are process-local. Remove only this stack's orphan runtimes;
        # team/environment notebook volumes persist across gateway restarts.
        filters = {"label": f"{LABEL}={self.scope}"}
        for container in self.docker.containers.list(all=True, filters=filters):
            container.remove(force=True)
        for network in self.docker.networks.list(filters=filters):
            self.cleanup_network(network)

    def start(self, session, database, namespace, table, *, database_name=None):
        key = filespace_key(session.team, session.environment)
        for workspace in self.workspaces.values():
            if workspace.key == key and workspace.session_id == session.id and workspace.database == database:
                return workspace
        if len(self.workspaces) >= self.limit:
            raise ServiceError(409, "All notebook slots are in use. Close a workspace first.")
        id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        labels = {LABEL: self.scope}
        volume_name = f"{self.scope}-work-{key}"
        try:
            volume = self.docker.volumes.get(volume_name)
            if volume.attrs.get("Labels", {}).get(LABEL) != self.scope:
                raise ServiceError(409, "The notebook storage does not belong to this workspace stack.")
        except NotFound:
            self.docker.volumes.create(volume_name, labels=labels)
        network = self.docker.networks.create(f"{self.scope}-session-{id}", internal=False, labels=labels)
        container = None
        try:
            network.connect(self.gateway, aliases=["workspace-gateway"])
            network.connect(self.polaris, aliases=["polaris"])
            network.connect(self.rustfs, aliases=["rustfs"])
            container = self.docker.containers.run(
                self.image,
                detach=True,
                name=f"{self.scope}-marimo-{id}",
                # One-off Compose labels group the notebook under this stack and
                # surface it to the monitor; Compose itself leaves it alone.
                labels={
                    **labels,
                    "com.docker.compose.project": self.scope,
                    "com.docker.compose.service": "notebook",
                    "com.docker.compose.oneoff": "True",
                },
                network=network.name,
                user="10001:10001",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=self.memory,
                nano_cpus=2_000_000_000,
                pids_limit=256,
                volumes={volume_name: {"bind": "/work", "mode": "rw"}},
                tmpfs={"/tmp": "rw,nosuid,nodev,size=256m,mode=1777"},
                log_config=LogConfig(type="none"),
                environment={
                    "HOME": "/tmp/home",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "/tmp/packages:/app",
                    "MARIMO_UV_TARGET": "/tmp/packages",
                    "MARIMO_SKIP_UPDATE_CHECK": "1",
                    "MARIMO_BASE_URL": f"/workspaces/{id}",
                    "MARIMO_GATEWAY_TOKEN": token,
                    "ICEBERG_DATABASE": database,
                    "ICEBERG_DATABASE_NAME": database_name or database,
                    "ICEBERG_ENVIRONMENT": session.environment,
                    "ICEBERG_NAMESPACE": json.dumps(namespace),
                    "ICEBERG_TABLE": table or "",
                    **({
                        "ICEBERG_ACCESS_TOKEN": session.token,
                        "ICEBERG_SESSION_TOKEN_URL": f"http://workspace-gateway:3002/internal/notebooks/{id}/token",
                    } if session.oidc_subject else {
                        "ICEBERG_CLIENT_ID": session.client_id,
                        "ICEBERG_CLIENT_SECRET": session.secret,
                    }),
                    "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
                    "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
                    "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
                },
            )
            container.reload()
            ip = container.attrs["NetworkSettings"]["Networks"][network.name]["IPAddress"]
            workspace = Workspace(
                id,
                session.id,
                session.user_id,
                session.team,
                database,
                key,
                container,
                network,
                f"http://{ip}:2718",
                token,
                session.environment,
            )
            deadline = time.monotonic() + 45
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    try:
                        response = client.get(
                            workspace.upstream + workspace.url, headers={"Authorization": f"Bearer {token}"}
                        )
                        if response.status_code == 200:
                            self.workspaces[id] = workspace
                            return workspace
                    except httpx.HTTPError:
                        pass
                    container.reload()
                    if container.status != "running":
                        break
                    time.sleep(0.25)
            raise ServiceError(503, "Marimo could not start. Check the notebook image and try again.")
        except Exception:
            if container is not None:
                container.remove(force=True)
            self.cleanup_network(network)
            raise

    def get(self, id, session):
        workspace = self.workspaces.get(id)
        if (
            not workspace
            or workspace.session_id != session.id
            or workspace.team != session.team
            or workspace.environment != session.environment
        ):
            raise ServiceError(404, "This notebook is unavailable in your session, team and environment.")
        return workspace

    def stop(self, id):
        workspace = self.workspaces[id]
        try:
            workspace.container.remove(force=True)
        except NotFound:
            pass
        try:
            self.cleanup_network(workspace.network)
        except NotFound:
            pass
        del self.workspaces[id]

    def stop_session(self, session_id):
        for workspace in list(self.workspaces.values()):
            if workspace.session_id == session_id:
                self.stop(workspace.id)

    def close(self):
        errors = []
        for id in list(self.workspaces):
            try:
                self.stop(id)
            except APIError as exc:
                errors.append(exc)
        self.docker.close()
        if errors:
            raise ServiceError(502, "Some notebook containers could not be stopped.")
