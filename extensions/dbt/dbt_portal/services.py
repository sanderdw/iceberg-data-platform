"""Every capability of the dbt extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the owning team | Allowed |
| --- | --- |
| reader | View projects, files, runs, schedules and pipelines; compile, preview and test (read token) |
| writer | Also create projects and branches, commit to branches, build in development, manage development schedules |
| admin, bucket-admin | Also merge to main, enable environments, schedule acceptance and production |

Runs always execute as the team's automation principal. Acceptance and production only build
revisions on `main`, so what reaches production went through a team administrator's merge.
"""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from croniter import croniter

from . import cleanup, lineage, profiles
from .cleanup import Cleaner
from .config import ENVIRONMENTS, ROOT
from .errors import DbtError
from .runs import READ_COMMANDS, WRITE_COMMANDS

PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
PREVIEWS = {"show", "compile", "parse"}
SELECTOR = re.compile(r"^[A-Za-z0-9_.:+*@/,\- ]{0,500}$")
WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
STARTER = ROOT / "templates" / "starter"


def timestamp(value):
    """A stored time (seconds since 1970) as every response shows it: RFC 3339 in UTC with milliseconds."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def starter_files(name, catalog):
    files = {}
    for path in sorted(STARTER.rglob("*")):
        if path.is_file():
            text = path.read_text().replace("{{PROJECT}}", name).replace("{{CATALOG}}", catalog)
            files[path.relative_to(STARTER).as_posix()] = text
    return files


def apps(discovery):
    """The Apps menu's entries (Bridge capability `app-directory`); with an older core, just the user portal."""
    pages = discovery.get("userPortalPages") or (
        [{"id": "portal", "title": "User portal", "url": discovery["userPortalUrl"]}]
        if discovery.get("userPortalUrl") else [])
    return {"workspace": [{k: str(p.get(k, "")) for k in ("id", "title", "url")} for p in pages],
            "extensions": [{k: str(e.get(k, "")) for k in ("id", "title", "purpose", "origin")}
                           for e in discovery.get("extensions") or []]}


class Services:
    def __init__(self, settings, store, repos, bridge, runs, cleaner=None):
        self.settings = settings
        self.cleaner = cleaner or Cleaner()
        self.store = store
        self.repos = repos
        self.bridge = bridge
        self.runs = runs
        self.graphs = lineage.GraphCache()

    # Context and authorization

    async def me(self, caller):
        return await self.bridge.me(caller.token)

    @staticmethod
    def role(me, team):
        return next((m["role"] for m in me["memberships"] if m["team"] == team), None)

    async def project_for(self, caller, project_id, minimum="reader"):
        project = self.store.project(project_id)
        me = await self.me(caller)
        role = self.role(me, project["team"]) if project else None
        if not project or not role:
            raise DbtError(404, "No such project in your teams.", "unknown_project")
        if minimum == "writer" and role not in WRITERS:
            raise DbtError(403, "This needs the writer or administrator role in the project's team.", "forbidden_role")
        if minimum == "admin" and role not in ADMINS:
            raise DbtError(403, "This needs the administrator role in the project's team.", "forbidden_role")
        return project, me, role

    @staticmethod
    def author(caller, me):
        name = me["user"]["name"]
        return {"name": name + (" (via agent)" if caller.agent else ""), "email": f"{name}@users.iceberg-platform"}

    @staticmethod
    def triggered(caller, me):
        return me["user"]["name"], caller.client if caller.agent else ""

    # Projections: what a stored row or a Bridge answer looks like in a response.

    def public_project(self, project, role=None):
        return {"id": project["id"], "team": project["team"], "name": project["name"],
                "description": project["description"], "defaultDatabase": project["default_database"],
                "createdAt": timestamp(project["created_at"]), "createdBy": project["created_by"], "role": role,
                "url": f"{self.settings.origin}/#/projects/{project['id']}/pipeline"}

    @staticmethod
    def public_run(run, results=None):
        """A run as every response shows it. `results` adds its node results: for one run, never in a list."""
        shown = {
            "id": run["id"], "project": run["project"], "environment": run["environment"], "status": run["status"],
            "triggeredBy": "schedule" if run["schedule"] else run["triggered_by"], "agent": run["agent"] or None,
            "scheduled": bool(run["schedule"]), "schedule": run["schedule"] or None, "command": run["command"],
            "select": run["selector"], "ref": run["ref"], "revision": run["revision"],
            "createdAt": timestamp(run["created_at"]), "startedAt": timestamp(run["started_at"]),
            "finishedAt": timestamp(run["finished_at"]), "exitCode": run["exit_code"],
            # dbt's totals and what the run published. The account the run acted as is not shown.
            "summary": {k: v for k, v in run["summary"].items() if k != "principal"},
            "error": run["error"] or None,
        }
        if run.get("hint"):
            shown["hint"] = run["hint"]
        return shown if results is None else {**shown, "results": results}

    @staticmethod
    def public_schedule(schedule):
        enabled = bool(schedule["enabled"])
        return {"id": schedule["id"], "project": schedule["project"], "environment": schedule["environment"],
                "cron": schedule["cron"], "command": schedule["command"], "select": schedule["selector"],
                "ref": schedule["ref"], "enabled": enabled,
                "nextRunAt": timestamp(schedule["next_run_at"]) if enabled else None,
                "lastRun": schedule["last_run"] or None, "createdBy": schedule["created_by"],
                "createdAt": timestamp(schedule["created_at"])}

    def public_me(self, me, discovery, enabled):
        """Who the caller is, built field by field: the Bridge's answer holds more than this API shows."""
        return {
            "user": {"id": me["user"]["id"], "name": me["user"]["name"]},
            "memberships": [{"team": m["team"], "teamName": m["teamName"], "role": m["role"]}
                            for m in me["memberships"]],
            "databases": [{k: d[k] for k in ("id", "name", "team", "environment", "status")}
                          for d in me["databases"]],
            "extension": self.settings.extension_id, "origin": self.settings.origin,
            "environments": list(ENVIRONMENTS),
            "enabledEnvironments": [{"team": team, "environments": envs} for team, envs in enabled.items()],
            "userPortalUrl": discovery.get("userPortalUrl", ""), "apps": apps(discovery),
        }

    @staticmethod
    def public_environment(me, team, environment, principal):
        """One team environment: whether dbt is enabled there, by whom, and the databases it can build in."""
        return {"team": team, "environment": environment, "enabled": principal is not None,
                "enabledBy": principal.get("createdBy") if principal else None,
                "databases": [{"id": d["id"], "name": d["name"]} for d in me["databases"]
                              if d["team"] == team and d["environment"] == environment]}

    @staticmethod
    def public_commit(commit):
        """A branch head or a commit from Git, whose times are whole seconds."""
        return {**{k: v for k, v in commit.items() if k != "committedAt"},
                "committedAt": timestamp(commit["committedAt"])}

    # Discovery

    async def enabled(self, caller, teams):
        """{team: [environment, ...]} where dbt is enabled, as list_environments sees it."""
        found = {}
        for team in teams:
            envs = {p["environment"] for p in await self.bridge.team_automation(caller.token, team)}
            found[team] = [env for env in ENVIRONMENTS if env in envs]
        return found

    async def whoami(self, caller):
        me = await self.me(caller)
        discovery = await self.bridge.discovery()
        enabled = await self.enabled(caller, [m["team"] for m in me["memberships"]])
        return self.public_me(me, discovery, enabled)

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise DbtError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return {"environments": [self.public_environment(me, team, env, enabled.get(env)) for env in ENVIRONMENTS]}

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise DbtError(422, "Choose development, acceptance or production.", "invalid_request")
        me = await self.me(caller)
        # The Bridge checks that the caller administers the team.
        principal = await self.bridge.enable(caller.token, team, environment)
        return self.public_environment(me, team, environment, principal)

    async def disable_environment(self, caller, team, environment):
        me = await self.me(caller)
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise DbtError(409, "dbt is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return self.public_environment(me, team, environment, None)

    # Projects

    async def list_projects(self, caller):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        return {"projects": [self.public_project(p, roles[p["team"]]) for p in self.store.projects(list(roles))]}

    async def create_project(self, caller, team, name, description="", default_database=""):
        me = await self.me(caller)
        if self.role(me, team) not in WRITERS:
            raise DbtError(403, "Creating a project needs the writer or administrator role in the team.",
                           "forbidden_role")
        if not PROJECT_NAME.match(name or ""):
            raise DbtError(422, "Project names use 3–48 lowercase letters, digits or '_', starting with a letter.",
                           "invalid_name")
        names = sorted({d["name"] for d in me["databases"] if d["team"] == team})
        default_database = default_database or (names[0] if names else "")
        if default_database not in names:
            raise DbtError(422, "Choose one of the team's databases as the default database.", "invalid_database",
                           {"databases": names})
        project = self.store.create_project(team, name, (description or "")[:280], default_database,
                                            me["user"]["name"])
        if not project:
            raise DbtError(409, "This team already has a project with this name.", "name_taken")
        try:
            await asyncio.to_thread(self.repos.create, project["id"],
                                    starter_files(name, profiles.alias(default_database)), self.author(caller, me))
        except Exception:
            self.store.delete_project(project["id"])
            raise
        result = self.public_project(project, self.role(me, team))
        if "development" not in (await self.enabled(caller, [team]))[team]:
            result["hint"] = ("dbt is not enabled for development in this team yet, so the project cannot compile "
                              "or build. A team administrator enables it with enable_environment.")
        return result

    async def get_project(self, caller, project_id):
        project, _, role = await self.project_for(caller, project_id)
        branches = await asyncio.to_thread(self.repos.branches, project_id)
        latest = {}
        for run in self.store.runs(project_id, limit=100):
            latest.setdefault(run["environment"], self.public_run(run))
        return {**self.public_project(project, role), "branches": [self.public_commit(b) for b in branches],
                "environments": (await self.environments(caller, project["team"]))["environments"],
                "latestRuns": latest,
                "schedules": [self.public_schedule(s) for s in self.store.schedules(project_id)]}

    async def delete_project(self, caller, project_id, confirm_name, force=False):
        """Forget a project as if it never existed: its Git history, runs, schedules and the producer
        properties on the tables it built. The tables, their data and docs stay."""
        project, _, _ = await self.project_for(caller, project_id, "admin")
        if confirm_name != project["name"]:
            raise DbtError(422, "The confirmation must be the project's exact name.", "confirmation_mismatch")
        runs = self.store.runs(project_id, limit=100_000)
        if any(run["status"] in ("queued", "running") for run in runs):
            raise DbtError(409, "A run of this project is still going. Wait for it or cancel it first.",
                           "run_in_progress")
        cleaned, failed = await self.forget_tables(project, runs)
        if failed and not force:
            raise DbtError(409, f"Could not remove the dbt properties from {len(failed)} table(s). Fix the cause and "
                                "try again, or delete with force to leave them.", "cleanup_failed", {"failed": failed})
        self.store.delete_project(project_id)
        await asyncio.to_thread(self.repos.delete, project_id)
        for run in runs:
            if (self.runs.artifacts / run["id"]).exists():
                await self.runs.discard(run["id"])
        return {"deleted": True, "project": project_id, "tables": {"cleaned": cleaned, "failed": failed}}

    async def forget_tables(self, project, runs):
        """Remove this project's producer, lineage and quality properties from the tables it built."""
        cleaned, failed = [], []
        discovery = await self.bridge.discovery()
        for environment, tables in cleanup.stamped_tables(r for r in runs if r["access"] == "write").items():
            try:
                principal = await self.bridge.principal_for(project["team"], environment)
                token = (await self.bridge.catalog_token(principal["id"], "write",
                                                         f"dbt delete project {project['id']}"))["accessToken"]
            except DbtError as exc:
                failed += [{"environment": environment, "table": table, "error": exc.code}
                           for table in sorted({".".join((*ns, name)) for _, ns, name in tables})]
                continue
            for warehouse, namespace, name in sorted(tables):
                table = ".".join((*namespace, name))
                try:
                    outcome = await self.cleaner.strip(discovery["catalog"]["internalUri"], token, project["id"],
                                                       warehouse, namespace, name)
                except httpx.HTTPError as exc:
                    failed.append({"environment": environment, "table": table, "error": type(exc).__name__})
                    continue
                if outcome == "removed":
                    cleaned.append({"environment": environment, "table": table})
        return cleaned, failed

    # Files and Git

    async def list_files(self, caller, project_id, ref="main"):
        await self.project_for(caller, project_id)
        revision, files = await asyncio.to_thread(self.repos.files, project_id, ref)
        return {"ref": ref, "revision": revision, "files": files}

    async def read_file(self, caller, project_id, path, ref="main"):
        await self.project_for(caller, project_id)
        revision, data = await asyncio.to_thread(self.repos.read, project_id, ref, path)
        try:
            return {"path": path, "ref": ref, "revision": revision, "content": data.decode()}
        except UnicodeDecodeError:
            raise DbtError(422, "This file is not UTF-8 text.", "binary_file") from None

    async def write_files(self, caller, project_id, branch, files, message, expected_revision=None):
        _, me, _ = await self.project_for(caller, project_id, "writer")
        if branch == "main":
            raise DbtError(403, "main changes only through merge_to_main. Commit to a branch and merge it.",
                           "protected_branch")
        revision = await asyncio.to_thread(self.repos.commit, project_id, branch, files, message,
                                           self.author(caller, me), expected=expected_revision)
        return {"branch": branch, "revision": revision, "files": sorted(files)}

    async def branches(self, caller, project_id):
        await self.project_for(caller, project_id)
        branches = await asyncio.to_thread(self.repos.branches, project_id)
        return {"branches": [self.public_commit(b) for b in branches]}

    async def create_branch(self, caller, project_id, name, source="main"):
        await self.project_for(caller, project_id, "writer")
        revision = await asyncio.to_thread(self.repos.create_branch, project_id, name, source)
        return {"branch": name, "revision": revision}

    async def delete_branch(self, caller, project_id, name):
        await self.project_for(caller, project_id, "writer")
        await asyncio.to_thread(self.repos.delete_branch, project_id, name)
        return {"deleted": True, "branch": name}

    async def history(self, caller, project_id, ref="main", limit=30):
        await self.project_for(caller, project_id)
        commits = await asyncio.to_thread(self.repos.log, project_id, ref, limit)
        return {"commits": [self.public_commit(c) for c in commits]}

    async def diff(self, caller, project_id, base, head):
        await self.project_for(caller, project_id)
        return await asyncio.to_thread(self.repos.diff, project_id, base, head)

    async def merge_to_main(self, caller, project_id, branch, message="", revision=None):
        _, me, _ = await self.project_for(caller, project_id, "admin")
        result = await asyncio.to_thread(self.repos.merge, project_id, branch, message, self.author(caller, me),
                                         revision)
        built = next((r for r in self.store.runs(project_id, "development", 200, "main")
                      if r["command"] in WRITE_COMMANDS and r["status"] == "success"), None)
        if not built or built["revision"] != result.get("revision"):
            # The viewer shows main, and it draws only what a run of main produced.
            result["hint"] = ("main has no build of this revision in development yet, so the pipeline viewer "
                              "still shows the previous one. Build main in development to draw it.")
        return result

    # Runs

    async def start_run(self, caller, project_id, command, environment, ref="main", select="", full_refresh=False,
                        *, extra=None, schedule=""):
        if command not in WRITE_COMMANDS | READ_COMMANDS:
            raise DbtError(422, "Choose build, run, seed, test, compile, show, parse or docs.", "invalid_command")
        if environment not in ENVIRONMENTS:
            raise DbtError(422, "Choose development, acceptance or production.", "invalid_request")
        if not SELECTOR.match(select or ""):
            raise DbtError(422, "The selector contains unsupported characters.", "invalid_selector")
        if caller is None:
            # A schedule: its creator's role was checked when it was saved, and it runs as the
            # team's automation principal, which a team administrator can revoke at any time.
            project, me = self.store.project(project_id), None
            if not project:
                raise DbtError(404, "No such project.", "unknown_project")
        else:
            project, me, _ = await self.project_for(caller, project_id,
                                                    "writer" if command in WRITE_COMMANDS else "reader")
        revision = await asyncio.to_thread(self.repos.resolve, project_id, ref)
        if environment != "development" and command in WRITE_COMMANDS and not await asyncio.to_thread(
                self.repos.was_main_head, project_id, revision):
            raise DbtError(403, f"{environment.title()} only builds main, or an earlier head of main. "
                                "Merge the branch first.", "main_required")
        principal = await self.bridge.principal_for(project["team"], environment)
        args = ["docs", "generate"] if command == "docs" else [command]
        if select:
            args += ["--select", *select.split()]
        if full_refresh and command in ("build", "run", "seed"):
            args.append("--full-refresh")
        args += extra or []
        name, agent = self.triggered(caller, me) if caller else (f"schedule {schedule}", "")
        run = self.runs.start(project=project, principal=principal, environment=environment, command=command,
                              args=args, ref=ref, revision=revision, triggered_by=name, agent=agent,
                              schedule=schedule, selector=select)
        if environment == "development" and command in WRITE_COMMANDS and ref != "main":
            # Development is one shared sandbox per team: every branch writes the same tables.
            run = {**run, "hint": f"Development is shared: this build of {ref} replaces the tables that builds of "
                                  "main and other branches write in development. Build main again to restore them."}
        return run

    async def run_project(self, caller, project_id, command="build", environment="development", ref="main",
                          select="", full_refresh=False, wait=0):
        """Start a run and return it as get_run does. `wait` holds the answer until the run is final, for at
        most that many seconds."""
        run = await self.start_run(caller, project_id, command, environment, ref, select, full_refresh)
        if wait:
            hint = {"hint": run["hint"]} if "hint" in run else {}
            run = {**await self.runs.wait(run["id"], wait), **hint}
        return self.public_run(run, self.results(run["id"]))

    async def run_for(self, caller, run_id, minimum="reader"):
        run = self.store.run(run_id)
        if not run:
            raise DbtError(404, "No such run in your teams.", "unknown_run")
        try:
            await self.project_for(caller, run["project"], minimum)
        except DbtError as exc:
            if exc.code == "unknown_project":
                raise DbtError(404, "No such run in your teams.", "unknown_run") from None
            raise
        return run

    def results(self, run_id):
        """A run's node results; empty until dbt has finished."""
        return self.store.query("SELECT node, status, execution_time AS executionTime, message, "
                                "rows_affected AS rowsAffected FROM node_results WHERE run = ? ORDER BY node", run_id)

    async def get_run(self, caller, run_id, wait=0):
        """The run with its results. `wait` holds the answer until the run is final, for at most that many seconds."""
        run = await self.run_for(caller, run_id)
        if wait and run["status"] in ("queued", "running"):
            run = await self.runs.wait(run_id, wait)
        return self.public_run(run, self.results(run_id))

    async def get_run_logs(self, caller, run_id, limit=500):
        run = await self.run_for(caller, run_id)
        return {"run": run_id, "status": run["status"], "lines": await self.runs.logs(run_id, limit)}

    async def list_runs(self, caller, project_id, environment=None, limit=20):
        await self.project_for(caller, project_id)
        return {"runs": [self.public_run(r) for r in self.store.runs(project_id, environment, limit)]}

    async def cancel_run(self, caller, run_id):
        run = await self.run_for(caller, run_id, "writer")
        if run["status"] not in ("queued", "running"):
            raise DbtError(409, "The run has already finished.", "run_finished")
        await self.runs.cancel(run_id)
        return {"run": run_id, "cancelling": True}

    async def compile_project(self, caller, project_id, environment="development", ref="main", select="", sql=""):
        """Compiled SQL of selected models, or of an inline query, with a read-only token."""
        extra = ["--inline", sql] if sql else []
        if sql and len(sql) > 20000:
            raise DbtError(413, "Inline SQL is limited to 20,000 characters.", "too_large")
        run = await self.start_run(caller, project_id, "compile", environment, ref, "" if sql else select,
                                   extra=extra)
        run = await self.runs.wait(run["id"], 300)
        lines = await self.runs.logs(run["id"], 400)
        compiled = {}
        info = self.runs.artifacts / run["id"] / "out" / "target" / "info_schema" / "v1"
        if not sql and info.exists():
            structure = await asyncio.to_thread(lineage.load, info)
            compiled = {n["id"]: n["compiledSql"] for n in structure["nodes"].values()
                        if n["type"] == "model" and n["own"] and n["compiledSql"]}
        return {"run": run["id"], "status": run["status"], "compiled": compiled,
                "output": "\n".join(lines[-200:]), "error": run["error"] or None}

    async def preview_model(self, caller, project_id, model, environment="development", ref="main", limit=20):
        if not re.match(r"^[A-Za-z0-9_.]{1,200}$", model or ""):
            raise DbtError(422, "Name one model.", "invalid_selector")
        run = await self.start_run(caller, project_id, "show", environment, ref, model,
                                   extra=["--limit", str(limit), "--output", "json"])
        run = await self.runs.wait(run["id"], 300)
        path = self.runs.artifacts / run["id"] / "out" / "show.json"
        rows = json.loads(path.read_text()) if path.exists() else None
        return {"run": run["id"], "status": run["status"], "rows": rows,
                "output": None if rows is not None else "\n".join((await self.runs.logs(run["id"], 60))[-60:])}

    # Schedules

    async def schedule_scope(self, caller, project_id, environment):
        return await self.project_for(caller, project_id, "writer" if environment == "development" else "admin")

    async def schedule_for(self, caller, schedule_id):
        """A schedule the caller may change: of a project in their teams, with the role its environment asks for."""
        schedule = self.store.schedule(schedule_id)
        if not schedule:
            raise DbtError(404, "No such schedule in your teams.", "unknown_schedule")
        try:
            _, me, _ = await self.schedule_scope(caller, schedule["project"], schedule["environment"])
        except DbtError as exc:
            if exc.code == "unknown_project":
                raise DbtError(404, "No such schedule in your teams.", "unknown_schedule") from None
            raise
        return schedule, me

    async def list_schedules(self, caller, project_id):
        await self.project_for(caller, project_id)
        return {"schedules": [self.public_schedule(s) for s in self.store.schedules(project_id)]}

    async def check_schedule(self, caller, project_id, environment, cron, command, select, ref, new_cron=True):
        """What a schedule stores, checked as a whole, and the caller's identity. A cron that is not being set
        keeps its interval, so a schedule from before the 5-minute minimum can still be paused."""
        if environment not in ENVIRONMENTS or command not in ("build", "run", "seed", "test"):
            raise DbtError(422, "Schedule build, run, seed or test in an environment.", "invalid_request")
        if not croniter.is_valid(cron or "") or len(cron.split()) != 5:
            raise DbtError(422, "Use a five-field cron expression in UTC, such as '0 6 * * *'.", "invalid_cron")
        now = time.time()
        if new_cron and self.too_often(cron, now):
            raise DbtError(422, "Schedules run at most every 5 minutes.", "invalid_cron")
        if not SELECTOR.match(select or ""):
            raise DbtError(422, "The selector contains unsupported characters.", "invalid_selector")
        _, me, _ = await self.schedule_scope(caller, project_id, environment)
        if environment != "development" and ref != "main":
            raise DbtError(403, "Acceptance and production schedules run main.", "main_required")
        await asyncio.to_thread(self.repos.resolve, project_id, ref)
        return me, croniter(cron, now).get_next(float)

    @staticmethod
    def too_often(cron, now):
        times = croniter(cron, now)
        first, second = times.get_next(float), times.get_next(float)
        return second - first < 300

    async def create_schedule(self, caller, project_id, environment, cron, command="build", select="", ref="main",
                              enabled=True):
        me, next_run = await self.check_schedule(caller, project_id, environment, cron, command, select, ref)
        schedule = self.store.create_schedule(
            project=project_id, created_by=me["user"]["name"], environment=environment, cron=cron, command=command,
            selector=select, ref=ref, enabled=int(bool(enabled)), next_run_at=next_run)
        return self.public_schedule(schedule)

    async def update_schedule(self, caller, schedule_id, changes):
        """Change a schedule. `changes` holds only what changes (environment, cron, command, select, ref, enabled);
        the rest stays."""
        schedule, _ = await self.schedule_for(caller, schedule_id)
        merged = {"environment": schedule["environment"], "cron": schedule["cron"], "command": schedule["command"],
                  "select": schedule["selector"], "ref": schedule["ref"], "enabled": bool(schedule["enabled"]),
                  **{k: v for k, v in changes.items() if v is not None}}
        _, next_run = await self.check_schedule(caller, schedule["project"], merged["environment"], merged["cron"],
                                                merged["command"], merged["select"], merged["ref"],
                                                new_cron=merged["cron"] != schedule["cron"])
        updated = self.store.update_schedule(
            schedule_id, environment=merged["environment"], cron=merged["cron"], command=merged["command"],
            selector=merged["select"], ref=merged["ref"], enabled=int(bool(merged["enabled"])), next_run_at=next_run)
        return self.public_schedule(updated)

    async def delete_schedule(self, caller, schedule_id):
        await self.schedule_for(caller, schedule_id)
        self.store.delete_schedule(schedule_id)
        return {"deleted": True, "schedule": schedule_id}

    # Pipelines

    def latest_structure(self, project_id, environment, ref=None):
        for run in self.store.runs(project_id, environment, 200, ref):
            if run["status"] in ("success", "failed") and (run["summary"] or {}).get("infoSchema"):
                info = self.runs.artifacts / run["id"] / "out" / "target" / "info_schema" / "v1"
                if Path(info).exists():
                    return run, self.graphs.get(run["id"], info)
        return None, None

    async def get_pipeline_graph(self, caller, project_id, environment="development", node=None, ref=None):
        """The graph of the latest run in an environment; with ref, only runs of that branch or revision count."""
        await self.project_for(caller, project_id)
        run, structure = await asyncio.to_thread(self.latest_structure, project_id, environment, ref)
        # The timeline shows what changed tables or checked them; previews (show, compile, parse) stay under Runs.
        runs = [self.public_run(r) for r in self.store.runs(project_id, environment, 100, ref)
                if r["command"] not in PREVIEWS][:20]
        if not structure and node:
            raise DbtError(404, "No pipeline yet: run build, compile or parse first.", "no_pipeline")
        if not structure:
            hint = (f"Run build, compile or parse on {ref} in this environment to draw its pipeline." if ref
                    else "Run build, compile or parse in this environment to draw the pipeline.")
            # Branches that do have a pipeline here, newest first, so the viewer can offer them.
            others = list(dict.fromkeys(r["ref"] for r in self.store.runs(project_id, environment, 200)
                                        if r["ref"] != ref and (r["summary"] or {}).get("infoSchema")))
            if ref and others:
                hint += f" Branches with a pipeline here: {', '.join(others[:5])}."
            return {"project": project_id, "environment": environment, "ref": ref, "nodes": [], "edges": [],
                    "structureRun": None, "runs": runs, "hint": hint, "refsWithPipeline": others[:5]}
        latest = self.store.latest_nodes(project_id, environment, ref)
        result = lineage.graph(structure, latest, catalogs=run["summary"].get("catalogs", {}), detail=bool(node))
        for item in result["nodes"]:
            item["lastRunAt"] = timestamp(item["lastRunAt"])
        if ref and environment == "development":
            result["overwrittenBy"] = self.overwritten_by(project_id, ref)
        if node:
            result["nodes"] = [n for n in result["nodes"] if n["id"] == node]
            if not result["nodes"]:
                raise DbtError(404, "No such node in this pipeline.", "unknown_node")
        if not node and not result["nodes"]:
            # A new project starts empty: the run succeeded, but there is nothing to draw yet.
            result["hint"] = "This project has no models yet. Ask your agent to add sources, models and tests."
        return {"project": project_id, "environment": environment, "ref": ref, "structureRun": run["id"],
                "revision": run["revision"], "runs": runs, **result}

    def overwritten_by(self, project_id, ref):
        """The newest write run of another branch in development after this branch's last one, if any.

        Development is shared, so its tables hold what the newest build wrote, whichever branch it was.
        """
        for run in self.store.runs(project_id, "development", 200):
            if run["command"] not in WRITE_COMMANDS or run["status"] not in ("success", "failed"):
                continue
            if run["ref"] == ref:
                return None
            return {"ref": run["ref"], "run": run["id"], "at": timestamp(run["finished_at"])}
        return None

    async def get_node(self, caller, project_id, node, environment="development", ref=None):
        pipeline = await self.get_pipeline_graph(caller, project_id, environment, node, ref)
        return {**pipeline["nodes"][0], "environment": environment, "structureRun": pipeline["structureRun"]}

    async def lineage(self, caller, project_id, node, environment="development", depth=3, ref=None):
        await self.project_for(caller, project_id)
        run, structure = await asyncio.to_thread(self.latest_structure, project_id, environment, ref)
        if not structure:
            raise DbtError(404, "No pipeline yet: run build, compile or parse first.", "no_pipeline")
        return {"environment": environment, "structureRun": run["id"],
                **lineage.neighbours(structure, node, depth)}

    async def publishes_docs(self, run):
        return run["command"] == "docs" and run["status"] == "success" and await asyncio.to_thread(
            self.repos.was_main_head, run["project"], run["revision"])

    def docs_path(self, run_id):
        return self.runs.artifacts / run_id / "out" / "docs"
