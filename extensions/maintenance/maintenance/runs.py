"""Run orchestration: resolve the policies, stage a job, launch the runner in isolation, keep its results and the
tables' health.

A run always executes as the team's automation principal for the database's environment. Inspections and
dry runs get a `read` catalog token from the Bridge, maintenance a `write` token. With it the gateway lists the
database's tables and asks Polaris which policies apply to each (`polaris.resolve`); the job carries them. The
platform sets the token's lifetime (POLARIS_TOKEN_LIFETIME, one hour by default), and a run ends well within it.
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

from . import polaris as catalogs
from .config import VERSION
from .errors import MaintenanceError

LOG = logging.getLogger(__name__)
RUNNER_UID = 10001
# A run ends this long before its token expires, so its last commits still work.
TOKEN_MARGIN = 900
MIN_RUN = 300
MAX_RESULT = 32 * 1024 * 1024


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
    """MNT_RUN_TIMEOUT_SECONDS, but never past the token the platform issued for this run."""
    limit = min(settings.run_timeout, int(issued.get("expiresIn") or 3600) - TOKEN_MARGIN)
    if limit < MIN_RUN:
        raise MaintenanceError(503, "The platform's catalog tokens expire too soon for a run. Ask the platform "
                                    "administrator to raise POLARIS_TOKEN_LIFETIME.", "token_lifetime")
    return limit


def stage(job):
    """The launcher's /work: job.json and an empty out/."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as out:
        add(out, "out", directory=True)
        add(out, "job.json", json.dumps(job).encode())
    return buffer.getvalue()


def read_result(directory):
    """The runner's result.json, or None when the run wrote none."""
    path = Path(directory) / "out" / "result.json"
    if not path.is_file() or path.stat().st_size > MAX_RESULT:
        return None
    try:
        result = json.loads(path.read_text())
    except ValueError:
        return None
    return result if isinstance(result, dict) else None


def applied_by(value):
    """The policy an action followed, as the runner reports it from the job."""
    if not isinstance(value, dict):
        return None
    applied = str(value.get("appliedAt", ""))
    return {"name": str(value.get("name", ""))[:256], "namespace": str(value.get("namespace", ""))[:256],
            "version": value["version"] if isinstance(value.get("version"), int) else 0,
            "appliedAt": applied if applied in ("catalog", "namespace", "table") else "catalog"}


def summarize(result):
    """What the API keeps of a run: totals and each table's status and actions. Health goes to the tables."""
    if not result:
        return {}
    totals = {k: v for k, v in (result.get("totals") or {}).items() if isinstance(v, int)}
    tables = []
    for item in (result.get("tables") or [])[:2000]:
        if not isinstance(item, dict):
            continue
        actions = [{"operation": str(a.get("operation", ""))[:40], "status": str(a.get("status", ""))[:20],
                    "detail": str(a.get("detail", ""))[:1000],
                    "metrics": {k: v for k, v in (a.get("metrics") or {}).items()
                                if isinstance(v, (int, float, str)) and not isinstance(v, bool)},
                    "policy": applied_by(a.get("policy"))}
                   for a in (item.get("actions") or [])[:20] if isinstance(a, dict)]
        tables.append({"name": str(item.get("name", ""))[:300], "status": str(item.get("status", ""))[:20],
                       "error": str(item.get("error") or "")[:2000], "actions": actions})
    return {"status": str(result.get("status", ""))[:20], "totals": totals, "tables": tables,
            "runnerVersion": str(result.get("runnerVersion", ""))[:40],
            "icebergGo": str(result.get("icebergGo", ""))[:80], "error": str(result.get("error") or "")[:2000]}


def healths(result, resolved):
    """Each table's name, status, error, actions, health and the policies that applied, for the tables store."""
    found = []
    for item in (result or {}).get("tables") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            health = item.get("health") if isinstance(item.get("health"), dict) else None
            found.append({"name": item["name"][:300], "status": str(item.get("status", ""))[:20],
                          "error": str(item.get("error") or "")[:2000], "health": health,
                          "policies": resolved.get(item["name"], {}),
                          "actions": summarize({"tables": [item]})["tables"][0]["actions"]})
    return found


def job_policies(applied):
    """A table's policies as the runner reads them: per type the settings, the policy and why it is not applied."""
    return {key: {**entry["settings"], "policy": entry["policy"], "problem": entry["problem"] or ""}
            for key, entry in applied.items()}


class RunManager:
    def __init__(self, settings, store, bridge, polaris, http=None):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.polaris = polaris
        self.http = http or httpx.AsyncClient(base_url=settings.launcher_url, timeout=60, trust_env=False,
                                              headers={"X-Launcher-Token": settings.launcher_token})
        self.slots = asyncio.Semaphore(settings.max_runs)
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
                                  error="The Maintenance extension restarted while this run was in progress.")

    def start(self, *, database, team, environment, kind, dry_run, tables, principal, triggered_by, agent="",
              schedule=""):
        """Queue a run. One database never has two runs at once: the store refuses a second (409)."""
        run = self.store.create_run(database=database, team=team, environment=environment, kind=kind,
                                    dry_run=int(dry_run), tables=tables, triggered_by=triggered_by, agent=agent,
                                    schedule=schedule)
        if run is None:
            raise MaintenanceError(409, "This database already has a run in progress.", "run_in_progress")
        self.phase[run["id"]] = "waiting"
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run, principal))
        self.tasks[run["id"]].add_done_callback(lambda _: self.forget(run["id"]))
        return run

    def forget(self, id):
        self.tasks.pop(id, None)
        self.phase.pop(id, None)
        self.stopping.discard(id)

    async def cancel(self, id):
        self.stopping.add(id)
        phase = self.phase.get(id)
        if phase == "waiting" and id in self.tasks:
            self.tasks[id].cancel()
        elif phase == "running":
            await self.http.delete(f"/runs/{id}")

    async def wait(self, id, timeout):
        """The run when it is final or after `timeout` seconds, whichever comes first. Waiting never fails on
        its own: a run that is stopped or fails meanwhile is simply returned as it is."""
        task = self.tasks.get(id)
        if task:
            await asyncio.wait({task}, timeout=timeout)
        return self.store.run(id)

    async def execute(self, run, principal):
        id = run["id"]
        try:
            async with AsyncExitStack() as held:
                await held.enter_async_context(self.slots)
                await self.launch(run, principal)
        except MaintenanceError as exc:
            self.store.update_run(id, status="error", finished_at=time.time(), error=str(exc)[:1000])
        except asyncio.CancelledError:
            self.store.update_run(id, status="cancelled", finished_at=time.time())
            raise
        except Exception as exc:
            LOG.exception("Run %s failed", id)
            self.store.update_run(id, status="error", finished_at=time.time(),
                                  error=f"The run failed unexpectedly ({type(exc).__name__}).")
        self.finished(run["database"])

    def job(self, run, discovery, deadline, tables):
        return {
            "runId": run["id"], "kind": run["kind"], "dryRun": bool(run["dry_run"]),
            "catalogUri": discovery["catalog"]["internalUri"], "warehouse": run["database"],
            "storageEndpoint": discovery["storage"]["internalEndpoint"],
            "tables": [{"name": t["name"], "identifier": t["identifier"], "policies": job_policies(t["policies"])}
                       for t in tables],
            "deadline": int(deadline), "url": f"{self.settings.origin}/#/runs/{run['id']}",
            "extensionVersion": VERSION,
        }

    async def launch(self, run, principal):
        id = run["id"]
        discovery = await self.bridge.discovery()
        scope = await self.bridge.scope(principal["id"])
        if run["database"] not in {d.get("id") for d in scope.get("databases", [])}:
            raise MaintenanceError(409, "The database is not in this team and environment any more.", "database_gone")
        access = "write" if run["kind"] == "maintain" and not run["dry_run"] else "read"
        issued = await self.bridge.catalog_token(principal["id"], access, f"maintenance {run['kind']} {id}"[:120])
        began = time.time()
        limit = time_limit(self.settings, issued)
        self.store.update_run(id, status="running", started_at=began, summary={"principal": principal["id"]})
        # Polaris says which policies apply to each table now, including ones other tools attached.
        catalog = self.polaris.catalog(run["database"], token=issued["accessToken"])
        tables = await catalogs.resolve(catalog, run["tables"])
        resolved = {t["name"]: t["policies"] for t in tables}
        limit = max(60, limit - int(time.time() - began))
        # The runner starts no new table after its deadline, and leaves a minute to report.
        staged = await asyncio.to_thread(stage, self.job(run, discovery, time.time() + limit - 60, tables))
        self.phase[id] = "launching"
        response = await self.http.post("/runs", json={
            "id": id, "archive": base64.b64encode(staged).decode(), "token": issued["accessToken"], "timeout": limit,
        })
        if response.status_code != 202:
            raise MaintenanceError(503, "The run could not start. " + response.json().get("error", ""), "launch_failed")
        self.phase[id] = "running"
        if id in self.stopping:
            await self.http.delete(f"/runs/{id}")
        deadline = time.monotonic() + limit + 120
        launched = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            launched = (await self.http.get(f"/runs/{id}")).json()
            if launched.get("state") == "finished":
                break
        result = await asyncio.to_thread(read_result, self.artifacts / id)
        summary = {**summarize(result), "principal": principal["id"], "timedOut": launched.get("timedOut")}
        if id in self.stopping:
            status, error = "cancelled", "Stopped before it finished. Tables it already maintained keep the changes."
        elif launched.get("state") != "finished" or launched.get("timedOut"):
            status, error = "error", f"The run exceeded its time limit of {limit // 60} minutes."
        elif result is None:
            status, error = "error", "The runner wrote no result. Read the run log."
        elif result.get("status") == "success" and launched.get("exitCode") == 0:
            status, error = "success", ""
        elif result.get("status") == "partial":
            status, error = "partial", "Some tables failed. The others were maintained."
        else:
            status, error = "failed", summary.get("error") or "The runner reported an error. Read the run log."
        if result is not None:
            await asyncio.to_thread(self.store.record_tables, run["database"], run, healths(result, resolved),
                                    "dry run" if run["dry_run"] else run["kind"], not run["tables"])
        self.store.update_run(id, status=status, finished_at=time.time(), exit_code=launched.get("exitCode"),
                              summary=summary, error=error[:2000])

    def finished(self, database):
        task = asyncio.create_task(self.prune(database))
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or
                               LOG.warning("Pruning %s failed: %s", database, t.exception()))

    async def prune(self, database):
        """Keeps the newest runs with their logs; older runs are removed with their artifacts."""
        for row in self.store.prune_runs(database, self.settings.run_retention):
            await self.discard(row["id"])
            self.store.delete_run(row["id"])

    async def discard(self, id):
        response = await self.http.delete(f"/runs/{id}/artifacts")
        if response.status_code not in (200, 404):
            LOG.warning("The artifacts of %s could not be removed (%s).", id, response.status_code)

    async def logs(self, id, limit=500):
        """The last `limit` lines of a run's log: from its artifacts, or from the launcher while it runs."""
        path = self.artifacts / id / "out" / "run.log"
        if path.exists():
            return path.read_text(errors="replace").splitlines()[-limit:]
        # `tail` is the launcher's own parameter name.
        response = await self.http.get(f"/runs/{id}/logs", params={"tail": limit})
        return response.text.splitlines() if response.status_code == 200 else []
