"""Run orchestration: stage a revision, launch it in isolation, and ingest its results.

A run always executes as the team's automation principal for the run's environment, with a
one-hour catalog token from the Bridge: `write` for commands that build tables, `read` for the
rest. The project is taken from Git at an exact revision; `profiles.yml` and `catalogs.yml` are
generated from the principal's current scope, so projects never contain credentials.
"""

import asyncio
import base64
import io
import json
import logging
import tarfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import profiles
from .config import ROOT, VERSION
from .errors import DbtError

LOG = logging.getLogger(__name__)
WRITE_COMMANDS = {"build", "run", "seed"}
READ_COMMANDS = {"test", "compile", "show", "parse", "docs"}
RUNNER_UID = 10001
PLATFORM_MACROS = ROOT / "platform_macros"
FINAL = {"success", "failed", "error", "cancelled"}


def access_for(command):
    return "write" if command in WRITE_COMMANDS else "read"


def add(tar, name, data=None, *, directory=False):
    info = tarfile.TarInfo(name)
    info.uid = info.gid = RUNNER_UID
    info.mtime = int(time.time())
    if directory:
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(info)
        return
    info.mode, info.size = 0o644, len(data)
    tar.addfile(info, io.BytesIO(data))


def forwarding(discovery):
    """Forward the vended (browser-facing) storage address to internal storage when it is loopback."""
    public, internal = urlsplit(discovery["storage"]["endpoint"]), urlsplit(discovery["storage"]["internalEndpoint"])
    if public.hostname not in ("localhost", "127.0.0.1"):
        return None
    port = public.port or (443 if public.scheme == "https" else 80)
    return {"listen": port, "target": f"{internal.hostname}:{internal.port or 80}"}


def stage(archive, *, environment, scope, discovery, job):
    """The launcher's /work: the project at its revision, generated profiles and the job."""
    catalogs_yml, catalogs = profiles.catalogs(scope, discovery["catalog"]["internalUri"])
    buffer = io.BytesIO()
    project_files = set()
    with tarfile.open(fileobj=buffer, mode="w") as out:
        for name in ("project", "project/macros", "project/macros/_iceberg_platform", "profiles", "out"):
            add(out, name, directory=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for member in source.getmembers():
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or path.name in profiles.RESERVED_FILES:
                    continue
                if member.isdir():
                    if member.name not in ("macros",):
                        add(out, f"project/{member.name}", directory=True)
                elif member.isfile():
                    data = source.extractfile(member).read()
                    project_files.add(member.name)
                    add(out, f"project/{member.name}", data)
                    if member.name.endswith(".sql") and b"macro generate_schema_name" in data:
                        project_files.add("__defines_generate_schema_name__")
        add(out, "project/catalogs.yml", catalogs_yml.encode())
        add(out, "project/macros/_iceberg_platform/replace_table.sql",
            (PLATFORM_MACROS / "replace_table.sql").read_bytes())
        if "__defines_generate_schema_name__" not in project_files:
            add(out, "project/macros/_iceberg_platform/generate_schema_name.sql",
                (PLATFORM_MACROS / "generate_schema_name.sql").read_bytes())
        add(out, "profiles/profiles.yml", profiles.profiles(environment).encode())
        job = {**job, "environment": environment, "forward": forwarding(discovery)}
        if job.get("publish") is not None:
            job["publish"] = {**job["publish"], "catalogs": catalogs, "catalogUri": discovery["catalog"]["internalUri"],
                              "environment": environment}
        add(out, "job.json", json.dumps(job).encode())
    return buffer.getvalue(), catalogs


def summarize(directory):
    """Counts and node results from a finished run's artifacts."""
    directory = Path(directory)
    result, nodes = {}, []
    if (directory / "out" / "result.json").exists():
        result = json.loads((directory / "out" / "result.json").read_text())
    run_results = directory / "out" / "target" / "run_results.json"
    if run_results.exists():
        for item in json.loads(run_results.read_text()).get("results", []):
            response = item.get("adapter_response") or {}
            nodes.append({"node": item["unique_id"], "status": item.get("status", "unknown"),
                          "executionTime": item.get("execution_time"), "message": item.get("message") or "",
                          "rowsAffected": response.get("rows_affected")})
    counts = {}
    for node in nodes:
        counts[node["status"]] = counts.get(node["status"], 0) + 1
    return {
        "exitCode": result.get("exitCode"), "counts": counts, "nodes": len(nodes),
        "published": result.get("published"),
        "infoSchema": (directory / "out" / "target" / "info_schema" / "v1" / "dbt.dag_nodes.parquet").exists(),
        "docs": (directory / "out" / "docs").is_dir(),
    }, nodes


class RunManager:
    def __init__(self, settings, store, repos, bridge, http=None):
        self.settings = settings
        self.store = store
        self.repos = repos
        self.bridge = bridge
        self.http = http or httpx.AsyncClient(base_url=settings.launcher_url, timeout=60, trust_env=False,
                                              headers={"X-Launcher-Token": settings.launcher_token})
        self.slots = asyncio.Semaphore(settings.max_runs)
        self.locks = {}
        self.tasks = {}
        self.artifacts = settings.state_dir / "artifacts"

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await self.http.aclose()

    def recover(self):
        """Runs that were in flight when the stack stopped cannot be followed any more."""
        for run in self.store.unfinished_runs():
            self.store.update_run(run["id"], status="error", finished_at=time.time(),
                                  error="The dbt stack restarted while this run was in progress.")

    def start(self, *, project, principal, environment, command, args, ref, revision, triggered_by, agent="",
              schedule="", selector=""):
        run = self.store.create_run(
            project=project["id"], environment=environment, command=command, selector=selector, ref=ref,
            revision=revision, access=access_for(command), triggered_by=triggered_by, agent=agent, schedule=schedule)
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run, project, principal, args))
        self.tasks[run["id"]].add_done_callback(lambda _: self.tasks.pop(run["id"], None))
        return run

    async def wait(self, id, timeout):
        task = self.tasks.get(id)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                pass
        return self.store.run(id)

    async def execute(self, run, project, principal, args):
        id = run["id"]
        lock = self.locks.setdefault((project["id"], run["environment"]), asyncio.Lock())
        try:
            async with self.slots:
                # Builds of one project and environment never overlap; reads may run alongside.
                if run["access"] == "write":
                    await lock.acquire()
                try:
                    await self.launch(run, project, principal, args)
                finally:
                    if run["access"] == "write":
                        lock.release()
        except DbtError as exc:
            self.store.update_run(id, status="error", finished_at=time.time(), error=str(exc)[:1000])
        except asyncio.CancelledError:
            self.store.update_run(id, status="cancelled", finished_at=time.time())
            raise
        except Exception as exc:
            LOG.exception("Run %s failed", id)
            self.store.update_run(id, status="error", finished_at=time.time(),
                                  error=f"The run failed unexpectedly ({type(exc).__name__}).")

    async def launch(self, run, project, principal, args):
        id = run["id"]
        discovery = await self.bridge.discovery()
        scope = await self.bridge.scope(principal["id"])
        issued = await self.bridge.catalog_token(principal["id"], run["access"], f"dbt {run['command']} {id}")
        archive = await asyncio.to_thread(self.repos.archive, project["id"], run["revision"])
        publish = None
        if run["access"] == "write":
            publish = {"origin": self.settings.origin, "project": project["id"], "run": id,
                       "revision": run["revision"], "version": f"iceberg-dbt {VERSION}"}
        staged, catalogs = await asyncio.to_thread(
            stage, archive, environment=run["environment"], scope=scope, discovery=discovery,
            job={"command": args, "publish": publish})
        self.store.update_run(id, status="running", started_at=time.time(),
                              summary={"catalogs": catalogs, "principal": principal["id"]})
        response = await self.http.post("/runs", json={
            "id": id, "archive": base64.b64encode(staged).decode(), "token": issued["accessToken"],
            "timeout": self.settings.run_timeout,
        })
        if response.status_code != 202:
            raise DbtError(503, "The run could not start. " + response.json().get("error", ""), "launch_failed")
        deadline = time.monotonic() + self.settings.run_timeout + 120
        state = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            state = (await self.http.get(f"/runs/{id}")).json()
            if state.get("state") == "finished":
                break
        summary, nodes = await asyncio.to_thread(summarize, self.artifacts / id)
        summary = {**summary, "catalogs": catalogs, "principal": principal["id"], "timedOut": state.get("timedOut")}
        finished = time.time()
        self.store.record_nodes(id, project["id"], run["environment"], nodes, finished)
        if state.get("state") != "finished" or state.get("timedOut"):
            status, error = "error", "The run exceeded its time limit."
        elif state.get("exitCode") == 0:
            status, error = "success", ""
        else:
            status, error = "failed", "dbt reported errors. Read the run log."
        self.store.update_run(id, status=status, finished_at=finished, exit_code=state.get("exitCode"),
                              summary=summary, error=error)

    async def logs(self, id, tail=2000):
        path = self.artifacts / id / "out" / "dbt.log"
        if path.exists():
            return path.read_text(errors="replace").splitlines()[-tail:]
        response = await self.http.get(f"/runs/{id}/logs", params={"tail": tail})
        return response.text.splitlines() if response.status_code == 200 else []

    async def cancel(self, id):
        await self.http.delete(f"/runs/{id}")
