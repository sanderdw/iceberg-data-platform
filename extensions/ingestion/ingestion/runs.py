"""Run orchestration: stage a pipeline, launch it in isolation, and keep its results and dlt state.

A run always executes as the team's automation principal for the pipeline's environment, with one
`write` catalog token from the Bridge. The platform sets the token's lifetime (POLARIS_TOKEN_LIFETIME,
one hour by default), and a run must end well within it. Source secrets are decrypted only to hand
them to the launcher, which puts them in the run container's environment.

dlt keeps its state (incremental cursors) and schemas in its pipelines folder. The run writes dlt's
own tables to its local disk, never to the team's namespace, so this extension keeps that folder
between runs: the state of the last successful run is staged into the next one.
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

import httpx

from .config import VERSION
from .errors import IngestionError

LOG = logging.getLogger(__name__)
RUNNER_UID = 10001
# A run ends this long before its token expires, so its last catalog calls and the metadata publish still work.
TOKEN_MARGIN = 900
MIN_RUN = 300
MAX_STATE = 8 * 1024 * 1024


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


def time_limit(settings, issued):
    """ING_RUN_TIMEOUT_SECONDS, but never past the token the platform issued for this run."""
    limit = min(settings.run_timeout, int(issued.get("expiresIn") or 3600) - TOKEN_MARGIN)
    if limit < MIN_RUN:
        raise IngestionError(503, "The platform's catalog tokens expire too soon for a run. Ask the platform "
                                  "administrator to raise POLARIS_TOKEN_LIFETIME.", "token_lifetime")
    return limit


def safe_members(tar):
    for member in tar.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
            continue
        yield member


def stage(job, state=None):
    """The launcher's /work: job.json and the dlt state of the previous successful run."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as out:
        for name in ("state", "out"):
            add(out, name, directory=True)
        if state:
            with tarfile.open(fileobj=io.BytesIO(state)) as source:
                for member in safe_members(source):
                    if member.isdir():
                        add(out, f"state/{member.name}", directory=True)
                    else:
                        add(out, f"state/{member.name}", source.extractfile(member).read())
        add(out, "job.json", json.dumps(job).encode())
    return buffer.getvalue()


def pack_state(directory):
    """state.json and schemas/ from a run's out/state, as a tar the next run gets back."""
    directory = Path(directory)
    if not (directory / "state.json").is_file():
        return None
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as out:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            out.add(path, arcname=str(path.relative_to(directory)), recursive=False)
    data = buffer.getvalue()
    return data if len(data) <= MAX_STATE else None


def summarize(directory):
    """The runner's result.json, reduced to what the API shows."""
    path = Path(directory) / "out" / "result.json"
    if not path.exists():
        return {}
    try:
        result = json.loads(path.read_text())
    except ValueError:
        return {}
    tables = [{"name": str(t.get("name", ""))[:200], "rows": int(t.get("rows") or 0)}
              for t in result.get("tables", [])[:500] if isinstance(t, dict)]
    published = result.get("published") or {}
    return {
        "loadId": result.get("loadId"), "dltVersion": result.get("dltVersion"), "tables": tables,
        "rows": sum(t["rows"] for t in tables),
        "published": {"tables": len(published.get("tables") or []), "errors": (published.get("errors") or [])[:20]},
        "error": (result.get("error") or "")[:2000],
    }


class RunManager:
    def __init__(self, settings, store, bridge, secrets, http=None):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.secrets = secrets
        self.http = http or httpx.AsyncClient(base_url=settings.launcher_url, timeout=60, trust_env=False,
                                              headers={"X-Launcher-Token": settings.launcher_token})
        self.slots = asyncio.Semaphore(settings.max_runs)
        self.team_slots = {}
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
                                  error="The Ingestion extension restarted while this run was in progress.")

    def start(self, *, pipeline, principal, triggered_by, agent="", schedule="", full_refresh=False):
        """Queue a run. One pipeline never runs twice at once; the caller checks that first (409)."""
        run = self.store.create_run(
            pipeline=pipeline["id"], team=pipeline["team"], environment=pipeline["environment"],
            revision=pipeline["revision"], full_refresh=int(full_refresh), triggered_by=triggered_by, agent=agent,
            schedule=schedule)
        self.phase[run["id"]] = "waiting"
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run, pipeline, principal))
        self.tasks[run["id"]].add_done_callback(lambda _: self.forget(run["id"]))
        return run

    def forget(self, id):
        self.tasks.pop(id, None)
        self.phase.pop(id, None)
        self.stopping.discard(id)

    async def cancel(self, id):
        """Stop a run in any phase. Waiting, it never reaches the launcher; while the launcher starts it, the
        stop follows as soon as it runs; running, its container is killed."""
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

    async def execute(self, run, pipeline, principal):
        id = run["id"]
        team = self.team_slots.setdefault(
            pipeline["team"], asyncio.Semaphore(min(self.settings.max_runs_per_team, self.settings.max_runs)))
        try:
            # Wait in this order, so a waiting run never holds a slot another team could use.
            async with AsyncExitStack() as held:
                await held.enter_async_context(team)
                await held.enter_async_context(self.slots)
                await self.launch(run, pipeline, principal)
        except IngestionError as exc:
            self.store.update_run(id, status="error", finished_at=time.time(), error=str(exc)[:1000])
        except asyncio.CancelledError:
            self.store.update_run(id, status="cancelled", finished_at=time.time())
            raise
        except Exception as exc:
            LOG.exception("Run %s failed", id)
            self.store.update_run(id, status="error", finished_at=time.time(),
                                  error=f"The run failed unexpectedly ({type(exc).__name__}).")
        self.finished(pipeline["id"])

    def job(self, run, pipeline, discovery):
        return {
            "runId": run["id"], "pipelineId": pipeline["id"], "pipelineName": pipeline["name"],
            "url": f"{self.settings.origin}/#/pipelines/{pipeline['id']}", "extensionVersion": VERSION,
            "environment": pipeline["environment"], "warehouse": pipeline["database"],
            "namespace": pipeline["namespace"], "catalogUri": discovery["catalog"]["internalUri"],
            "storageEndpoint": discovery["storage"]["internalEndpoint"], "sourceType": pipeline["source_type"],
            "source": pipeline["source"], "resources": pipeline["resources"],
            "allowPrivate": self.settings.allow_private, "fullRefresh": bool(run["full_refresh"]),
        }

    async def launch(self, run, pipeline, principal):
        id = run["id"]
        discovery = await self.bridge.discovery()
        scope = await self.bridge.scope(principal["id"])
        if pipeline["database"] not in {d.get("id") for d in scope.get("databases", [])}:
            raise IngestionError(409, "The pipeline's database is not in this team and environment any more.",
                                 "database_gone")
        issued = await self.bridge.catalog_token(principal["id"], "write", f"ingestion {pipeline['name']} {id}"[:120])
        limit = time_limit(self.settings, issued)
        state = None if run["full_refresh"] else self.store.state(pipeline["id"])
        staged = await asyncio.to_thread(stage, self.job(run, pipeline, discovery), state)
        self.store.update_run(id, status="running", started_at=time.time(), summary={"principal": principal["id"]})
        # From here the launcher may start the container, so a stop waits until it runs (see cancel).
        self.phase[id] = "launching"
        response = await self.http.post("/runs", json={
            "id": id, "archive": base64.b64encode(staged).decode(), "token": issued["accessToken"],
            "secrets": self.secrets.reveal(pipeline["secrets"]), "timeout": limit,
        })
        if response.status_code != 202:
            raise IngestionError(503, "The run could not start. " + response.json().get("error", ""), "launch_failed")
        self.phase[id] = "running"
        if id in self.stopping:  # stopped while the launcher was starting it
            await self.http.delete(f"/runs/{id}")
        deadline = time.monotonic() + limit + 120
        launched = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            launched = (await self.http.get(f"/runs/{id}")).json()
            if launched.get("state") == "finished":
                break
        summary = await asyncio.to_thread(summarize, self.artifacts / id)
        summary = {**summary, "principal": principal["id"], "timedOut": launched.get("timedOut")}
        if id in self.stopping:
            status, error = "cancelled", "Stopped before it finished. Tables it already loaded keep their new rows."
        elif launched.get("state") != "finished" or launched.get("timedOut"):
            status, error = "error", f"The run exceeded its time limit of {limit // 60} minutes."
        elif launched.get("exitCode") == 0:
            status, error = "success", ""
        else:
            status, error = "failed", summary.get("error") or "dlt reported an error. Read the run log."
        if status == "success":
            packed = await asyncio.to_thread(pack_state, self.artifacts / id / "out" / "state")
            if packed:
                self.store.save_state(pipeline["id"], packed, id)
        self.store.update_run(id, status=status, finished_at=time.time(), exit_code=launched.get("exitCode"),
                              summary=summary, error=error[:2000])

    def finished(self, pipeline_id):
        """Schedules the cleanup of older runs' logs after a run of this pipeline."""
        task = asyncio.create_task(self.prune(pipeline_id))
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or
                               LOG.warning("Pruning %s failed: %s", pipeline_id, t.exception()))

    async def prune(self, pipeline_id):
        """Keeps the newest runs with their logs; older runs are removed with their artifacts."""
        for row in self.store.prune_runs(pipeline_id, self.settings.run_retention):
            await self.discard(row["id"])
            self.store.delete_run(row["id"])

    async def discard(self, id):
        response = await self.http.delete(f"/runs/{id}/artifacts")
        if response.status_code not in (200, 404):
            LOG.warning("The artifacts of %s could not be removed (%s).", id, response.status_code)

    async def logs(self, id, tail=2000):
        path = self.artifacts / id / "out" / "run.log"
        if path.exists():
            lines = path.read_text(errors="replace").splitlines()[-tail:]
        else:
            response = await self.http.get(f"/runs/{id}/logs", params={"tail": tail})
            lines = response.text.splitlines() if response.status_code == 200 else []
        # The runner already masks its secrets; this second pass uses the values stored now.
        run = self.store.run(id)
        pipeline = self.store.pipeline(run["pipeline"]) if run else None
        try:
            values = tuple(self.secrets.reveal(pipeline["secrets"]).values()) if pipeline else ()
        except IngestionError:
            values = ()
        return self.secrets.scrub_lines(lines, values)
