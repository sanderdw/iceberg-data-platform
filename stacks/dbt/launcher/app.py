"""The launcher: the only part of the dbt stack with the Docker socket.

It starts one pinned runner image per run, with fixed isolation flags, on a private internal
network that holds only the platform's catalog and storage (found by their role labels, see the
Bridge network contract). The portal can ask for nothing else: no image, flag or mount choice.
"""

import base64
import io
import logging
import os
import secrets
import shutil
import tarfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.types import Mount
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

EXTENSION_LABEL = "io.iceberg-platform.extension"
ROLE_LABEL = "io.iceberg-platform.role"
RUN_LABEL = "io.iceberg-dbt.run"
RUN_ID = r"^run-[a-f0-9]{24}$"
LOG = logging.getLogger(__name__)
# The runner reads its job from here and writes its results under /work/out.
WORK = "/work"
ALIASES = {"catalog": "polaris-control-plane", "storage": "rustfs"}


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=RUN_ID)
    archive: str = Field(max_length=40 * 1024 * 1024, repr=False)
    token: str = Field(min_length=1, max_length=16384, repr=False)
    timeout: int = Field(ge=30, le=3000)


class Launcher:
    def __init__(self, env, client=None):
        self.docker = client or docker.from_env(timeout=60)
        self.image = env.get("RUNNER_IMAGE", "iceberg-dbt-runner:0.1.0")
        self.artifacts = Path(env.get("ARTIFACTS_DIR", "/artifacts"))
        self.memory = env.get("RUN_MEMORY", "2g")
        self.cpus = float(env.get("RUN_CPUS", "2"))
        self.extension = env.get("BRIDGE_EXTENSION_ID", "dbt")
        self.runs = {}
        self.lock = threading.Lock()

    def recover(self):
        """Runs are process-local: remove what a previous launcher left behind."""
        for container in self.docker.containers.list(all=True, filters={"label": RUN_LABEL}):
            container.remove(force=True, v=True)
        for network in self.docker.networks.list(filters={"label": RUN_LABEL}):
            self.cleanup_network(network)

    def cleanup_network(self, network):
        try:
            network.reload()
            for container_id in network.attrs.get("Containers", {}):
                try:
                    network.disconnect(container_id, force=True)
                except NotFound:
                    pass
            network.remove()
        except NotFound:
            pass

    def service(self, role):
        found = self.docker.containers.list(filters={"label": f"{ROLE_LABEL}={role}", "status": "running"})
        if not found:
            raise RuntimeError(f"No running platform container has the label {ROLE_LABEL}={role}.")
        return found[0]

    def start(self, data):
        with self.lock:
            if data.id in self.runs:
                raise ValueError("This run was already started.")
            self.runs[data.id] = {"state": "starting", "startedAt": time.time()}
        archive = base64.b64decode(data.archive)
        labels = {EXTENSION_LABEL: self.extension, RUN_LABEL: data.id}
        network = self.docker.networks.create(f"iceberg-dbt-{data.id}", internal=True, labels=labels)
        container = None
        try:
            for role, alias in ALIASES.items():
                network.connect(self.service(role), aliases=[alias])
            container = self.docker.containers.create(
                self.image, name=f"iceberg-dbt-{data.id}", labels=labels, network=network.name,
                user="10001:10001", read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
                mem_limit=self.memory, nano_cpus=int(self.cpus * 1_000_000_000), pids_limit=512,
                tmpfs={"/tmp": "rw,nosuid,nodev,size=512m,mode=1777"},
                mounts=[Mount(target=WORK, source=None, type="volume")],
                environment={"DBT_ENV_SECRET_POLARIS_TOKEN": data.token},
            )
            if not container.put_archive(WORK, archive):
                raise RuntimeError("The run could not be staged.")
            container.start()
        except Exception:
            if container is not None:
                container.remove(force=True, v=True)
            self.cleanup_network(network)
            with self.lock:
                self.runs.pop(data.id, None)
            raise
        with self.lock:
            self.runs[data.id].update(state="running", container=container.id, network=network.id)
        threading.Thread(target=self.watch, args=(data.id, container, network, data.timeout), daemon=True).start()

    def watch(self, id, container, network, timeout):
        exit_code, timed_out = None, False
        try:
            try:
                exit_code = container.wait(timeout=timeout)["StatusCode"]
            except Exception:  # noqa: BLE001 - A timeout or daemon error ends the run either way.
                timed_out = True
                container.kill()
                exit_code = container.wait(timeout=30)["StatusCode"]
            self.collect(id, container)
        except (DockerException, OSError) as exc:
            LOG.error("Run %s could not be collected: %s", id, type(exc).__name__)
        finally:
            try:
                container.remove(force=True, v=True)
            except DockerException:
                pass
            self.cleanup_network(network)
            with self.lock:
                self.runs[id].update(state="finished", exitCode=exit_code, timedOut=timed_out,
                                     finishedAt=time.time())

    def collect(self, id, container):
        target = self.artifacts / id
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        try:
            stream, _ = container.get_archive(f"{WORK}/out")
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                for member in tar.getmembers():
                    # Only regular files and directories under out/, never links or absolute paths.
                    name = Path(member.name)
                    if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                        continue
                    tar.extract(member, target, filter="data")
        except NotFound:
            pass
        logs = container.logs(stdout=True, stderr=True, tail=20000)
        (target / "container.log").write_bytes(logs[-2_000_000:])

    def status(self, id):
        with self.lock:
            run = self.runs.get(id)
            return {k: v for k, v in run.items() if k not in ("container", "network")} if run else None

    def logs(self, id, tail):
        with self.lock:
            run = self.runs.get(id)
        if not run:
            return None
        if run.get("state") == "running":
            try:
                return self.docker.containers.get(run["container"]).logs(tail=tail).decode(errors="replace")
            except NotFound:
                pass
        path = self.artifacts / id / "out" / "dbt.log"
        return path.read_text(errors="replace")[-200_000:] if path.exists() else ""

    def cancel(self, id):
        with self.lock:
            run = self.runs.get(id)
        if run and run.get("state") == "running":
            try:
                self.docker.containers.get(run["container"]).kill()
            except (NotFound, APIError):
                pass
            return True
        return False


def create_app(launcher=None, env=None):
    env = os.environ if env is None else env
    token = env.get("LAUNCHER_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("LAUNCHER_TOKEN must be at least 32 characters.")
    launcher = launcher or Launcher(env)

    @asynccontextmanager
    async def lifespan(app):
        launcher.recover()
        yield

    app = FastAPI(title="dbt launcher", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path != "/health" and not secrets.compare_digest(
                request.headers.get("x-launcher-token", ""), token):
            return JSONResponse({"error": "Launcher token required."}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/runs", status_code=202)
    def start(data: RunInput):
        try:
            launcher.start(data)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (RuntimeError, DockerException) as exc:
            LOG.error("Run %s could not start: %s", data.id, exc)
            return JSONResponse({"error": "The run could not start. Check the runner image and the platform."},
                                status_code=503)
        return {"id": data.id, "state": "running"}

    @app.get("/runs/{id}")
    def status(id: str):
        run = launcher.status(id)
        return run if run else JSONResponse({"error": "Unknown run."}, status_code=404)

    @app.get("/runs/{id}/logs", response_class=PlainTextResponse)
    def logs(id: str, tail: int = 2000):
        text = launcher.logs(id, max(1, min(tail, 20000)))
        return text if text is not None else PlainTextResponse("", status_code=404)

    @app.delete("/runs/{id}")
    def cancel(id: str):
        return {"cancelled": launcher.cancel(id)}

    return app
