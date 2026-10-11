"""Run orchestration: stage a revision, launch it in isolation, and ingest its results.

A run always executes as the team's automation principal for the run's environment, with one
catalog token from the Bridge: `write` for commands that build tables, `read` for the rest. The
platform sets the token's lifetime (POLARIS_TOKEN_LIFETIME, one hour by default), and a run must
end well within it. The project is taken from Git at an exact revision; `profiles.yml` and `catalogs.yml` are
generated from the principal's current scope, so projects never contain credentials.
"""

import asyncio
import base64
import io
import json
import logging
import tarfile
import time
from contextlib import AsyncExitStack
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


CATALOG_PROXY_PORT = 18181
# A run ends this long before its token expires, so its last catalog calls and the metadata publish still work.
TOKEN_MARGIN = 900
MIN_RUN = 300


def time_limit(settings, issued):
    """RUN_TIMEOUT_SECONDS, but never past the token the platform issued for this run."""
    limit = min(settings.run_timeout, int(issued.get("expiresIn") or 3600) - TOKEN_MARGIN)
    if limit < MIN_RUN:
        raise DbtError(503, "The platform's catalog tokens expire too soon for a run. Ask the platform administrator "
                            "to raise POLARIS_TOKEN_LIFETIME.", "token_lifetime")
    return limit


def catalog_proxy(discovery):
    """For a vended storage address that is not loopback, such as a quick share's HTTPS address.

    A run has no internet and no certificate for that host, so dbt reads the catalog through the
    run's loopback proxy (runner/catalog_proxy.py), which points vended storage at internal storage.
    """
    storage = discovery["storage"]
    if urlsplit(storage["endpoint"]).hostname in ("localhost", "127.0.0.1"):
        return None
    catalog = urlsplit(discovery["catalog"]["internalUri"])
    return {"listen": CATALOG_PROXY_PORT, "upstream": f"{catalog.scheme}://{catalog.netloc}",
            "uri": f"http://127.0.0.1:{CATALOG_PROXY_PORT}{catalog.path}",
            "storage": {"public": storage["endpoint"], "internal": storage["internalEndpoint"]}}


def stage(archive, *, environment, scope, discovery, job):
    """The launcher's /work: the project at its revision, generated profiles and the job."""
    proxy = catalog_proxy(discovery)
    catalog_uri = proxy["uri"] if proxy else discovery["catalog"]["internalUri"]
    catalogs_yml, catalogs = profiles.catalogs(scope, catalog_uri)
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
        job = {**job, "environment": environment, "forward": forwarding(discovery), "catalogProxy": proxy}
        if job.get("publish") is not None:
            job["publish"] = {**job["publish"], "catalogs": catalogs, "catalogUri": catalog_uri,
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
        "semanticModels": result.get("semanticModels"),
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
        self.team_slots = {}
        self.locks = {}
        self.tasks = {}
        # Where each unfinished run is ("waiting", "launching" or "running"), and which ones to stop.
        self.phase = {}
        self.stopping = set()
        self.artifacts = settings.state_dir / "artifacts"

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await self.http.aclose()

    def recover(self):
        """Runs that were in flight when the extension stopped cannot be followed any more."""
        for run in self.store.unfinished_runs():
            self.store.update_run(run["id"], status="error", finished_at=time.time(),
                                  error="The dbt extension restarted while this run was in progress.")

    def start(self, *, project, principal, environment, command, args, ref, revision, triggered_by, agent="",
              schedule="", selector=""):
        run = self.store.create_run(
            project=project["id"], environment=environment, command=command, selector=selector, ref=ref,
            revision=revision, access=access_for(command), triggered_by=triggered_by, agent=agent, schedule=schedule)
        self.phase[run["id"]] = "waiting"
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run, project, principal, args))
        self.tasks[run["id"]].add_done_callback(lambda _: self.forget(run["id"]))
        return run

    def forget(self, id):
        self.tasks.pop(id, None)
        self.phase.pop(id, None)
        self.stopping.discard(id)

    async def cancel(self, id):
        """Stop a run in any phase. Waiting or being staged, it never reaches the launcher; while the launcher
        starts it, the stop follows as soon as it runs; running, its container is killed."""
        self.stopping.add(id)
        phase = self.phase.get(id)
        if phase == "waiting" and id in self.tasks:
            self.tasks[id].cancel()
        elif phase == "running":
            await self.http.delete(f"/runs/{id}")

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
        team = self.team_slots.setdefault(
            project["team"], asyncio.Semaphore(min(self.settings.max_runs_per_team, self.settings.max_runs)))
        try:
            # Wait in this order, so a waiting run never holds a slot another team could use: builds
            # of one project and environment never overlap (reads may run alongside), a team runs at
            # most max_runs_per_team at once, and all teams share max_runs.
            async with AsyncExitStack() as held:
                if run["access"] == "write":
                    await held.enter_async_context(lock)
                await held.enter_async_context(team)
                await held.enter_async_context(self.slots)
                await self.launch(run, project, principal, args)
        except DbtError as exc:
            self.store.update_run(id, status="error", finished_at=time.time(), error=str(exc)[:1000])
        except asyncio.CancelledError:
            self.store.update_run(id, status="cancelled", finished_at=time.time())
            raise
        except Exception as exc:
            LOG.exception("Run %s failed", id)
            self.store.update_run(id, status="error", finished_at=time.time(),
                                  error=f"The run failed unexpectedly ({type(exc).__name__}).")
        self.finished(project["id"], run["environment"])

    async def launch(self, run, project, principal, args):
        id = run["id"]
        discovery = await self.bridge.discovery()
        scope = await self.bridge.scope(principal["id"])
        issued = await self.bridge.catalog_token(principal["id"], run["access"], f"dbt {run['command']} {id}")
        limit = time_limit(self.settings, issued)
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
        # From here the launcher may start the container, so a stop waits until it runs (see cancel).
        self.phase[id] = "launching"
        response = await self.http.post("/runs", json={
            "id": id, "archive": base64.b64encode(staged).decode(), "token": issued["accessToken"],
            "timeout": limit,
        })
        if response.status_code != 202:
            raise DbtError(503, "The run could not start. " + response.json().get("error", ""), "launch_failed")
        self.phase[id] = "running"
        if id in self.stopping:  # stopped while the launcher was starting it
            await self.http.delete(f"/runs/{id}")
        deadline = time.monotonic() + limit + 120
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
        if id in self.stopping:
            status, error = "cancelled", "Stopped before it finished. Tables it already replaced stay replaced."
        elif state.get("state") != "finished" or state.get("timedOut"):
            status, error = "error", f"The run exceeded its time limit of {limit // 60} minutes."
        elif state.get("exitCode") == 0:
            status, error = "success", ""
        else:
            status, error = "failed", "dbt reported errors. Read the run log."
        self.store.update_run(id, status=status, finished_at=finished, exit_code=state.get("exitCode"),
                              summary=summary, error=error)

    def finished(self, project_id, environment):
        """Schedules the cleanup of older artifacts after a run of this project and environment."""
        task = asyncio.create_task(self.prune(project_id, environment))
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or
                               LOG.warning("Pruning %s failed: %s", project_id, t.exception()))

    async def prune(self, project_id, environment):
        """Keeps the artifacts of the newest runs, the run the pipeline is drawn from and the newest docs.

        Older runs keep their status, results and summary; only their log and files go.
        """
        runs = [r for r in self.store.runs(project_id, environment, 100_000)
                if r["status"] not in ("queued", "running")]
        structure = (r["id"] for r in runs if r["status"] in ("success", "failed") and r["summary"].get("infoSchema"))
        docs = (r["id"] for r in runs if r["command"] == "docs" and r["status"] == "success")
        keep = {r["id"] for r in runs[:self.settings.run_retention]} | {next(structure, None), next(docs, None)}
        for run in runs:
            if run["id"] not in keep and (self.artifacts / run["id"]).exists():
                await self.discard(run["id"])

    async def discard(self, id):
        response = await self.http.delete(f"/runs/{id}/artifacts")
        if response.status_code not in (200, 404):
            LOG.warning("The artifacts of %s could not be removed (%s).", id, response.status_code)

    async def logs(self, id, tail=2000):
        path = self.artifacts / id / "out" / "dbt.log"
        if path.exists():
            return path.read_text(errors="replace").splitlines()[-tail:]
        response = await self.http.get(f"/runs/{id}/logs", params={"tail": tail})
        return response.text.splitlines() if response.status_code == 200 else []

