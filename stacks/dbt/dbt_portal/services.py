"""Every capability of the dbt stack, shared by the REST API and the MCP server.

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
from pathlib import Path

from croniter import croniter

from . import lineage, profiles
from .config import ENVIRONMENTS, ROOT
from .errors import DbtError
from .runs import READ_COMMANDS, WRITE_COMMANDS

PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
SELECTOR = re.compile(r"^[A-Za-z0-9_.:+*@/,\- ]{0,500}$")
WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
STARTER = ROOT / "templates" / "starter"


def starter_files(name, catalog):
    files = {}
    for path in sorted(STARTER.rglob("*")):
        if path.is_file():
            text = path.read_text().replace("{{PROJECT}}", name).replace("{{CATALOG}}", catalog)
            files[path.relative_to(STARTER).as_posix()] = text
    return files


class Services:
    def __init__(self, settings, store, repos, bridge, runs):
        self.settings = settings
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

    def public(self, project, role=None):
        return {**project, "role": role, "url": f"{self.settings.origin}/#/projects/{project['id']}/pipeline"}

    # Discovery

    async def whoami(self, caller):
        me = await self.me(caller)
        discovery = await self.bridge.discovery()
        return {**me, "extension": self.settings.extension_id, "origin": self.settings.origin,
                "environments": list(ENVIRONMENTS), "userPortalUrl": discovery.get("userPortalUrl", "")}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise DbtError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return [{"environment": env, "enabled": env in enabled,
                 "databases": [d["name"] for d in me["databases"] if d["team"] == team and d["environment"] == env],
                 **({"principal": enabled[env]["id"], "enabledBy": enabled[env]["createdBy"]}
                    if env in enabled else {})}
                for env in ENVIRONMENTS]

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise DbtError(422, "Choose development, acceptance or production.", "invalid_request")
        return await self.bridge.enable(caller.token, team, environment)

    async def disable_environment(self, caller, team, environment):
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise DbtError(404, "dbt is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return {"disabled": True, "environment": environment}

    # Projects

    async def list_projects(self, caller):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        return [self.public(p, roles[p["team"]]) for p in self.store.projects(list(roles))]

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
            raise DbtError(422, "Choose one of the team's databases as the default database.", "unknown_database",
                           {"databases": names})
        project = self.store.create_project(team, name, (description or "")[:280], default_database,
                                            me["user"]["name"])
        if not project:
            raise DbtError(409, "This team already has a project with this name.", "project_exists")
        try:
            await asyncio.to_thread(self.repos.create, project["id"],
                                    starter_files(name, profiles.alias(default_database)), self.author(caller, me))
        except Exception:
            self.store.delete_project(project["id"])
            raise
        return self.public(project, self.role(me, team))

    async def get_project(self, caller, project_id):
        project, _, role = await self.project_for(caller, project_id)
        branches = await asyncio.to_thread(self.repos.branches, project_id)
        latest = {}
        for run in self.store.runs(project_id, limit=100):
            latest.setdefault(run["environment"], run)
        return {**self.public(project, role), "branches": branches,
                "environments": await self.environments(caller, project["team"]),
                "latestRuns": latest, "schedules": self.store.schedules(project_id)}

    async def delete_project(self, caller, project_id, confirm_name):
        project, _, _ = await self.project_for(caller, project_id, "admin")
        if confirm_name != project["name"]:
            raise DbtError(422, "Type the project name exactly to confirm.", "confirmation_required")
        self.store.delete_project(project_id)
        await asyncio.to_thread(self.repos.delete, project_id)
        return {"deleted": True, "project": project_id}

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
        return await asyncio.to_thread(self.repos.branches, project_id)

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
        return await asyncio.to_thread(self.repos.log, project_id, ref, limit)

    async def diff(self, caller, project_id, base, head):
        await self.project_for(caller, project_id)
        return await asyncio.to_thread(self.repos.diff, project_id, base, head)

    async def merge_to_main(self, caller, project_id, branch, message=""):
        _, me, _ = await self.project_for(caller, project_id, "admin")
        return await asyncio.to_thread(self.repos.merge, project_id, branch, message, self.author(caller, me))

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
                self.repos.is_on_main, project_id, revision):
            raise DbtError(403, f"{environment.title()} only builds revisions on main. Merge the branch first.",
                           "main_required")
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
        return run

    async def run(self, caller, project_id, command, environment, ref="main", select="", full_refresh=False,
                  wait=0):
        run = await self.start_run(caller, project_id, command, environment, ref, select, full_refresh)
        if wait:
            run = await self.runs.wait(run["id"], min(wait, 600))
        return run

    async def run_for(self, caller, run_id):
        run = self.store.run(run_id)
        if not run:
            raise DbtError(404, "No such run.", "unknown_run")
        await self.project_for(caller, run["project"])
        return run

    async def get_run(self, caller, run_id, wait=0):
        run = await self.run_for(caller, run_id)
        if wait and run["status"] in ("queued", "running"):
            run = await self.runs.wait(run_id, min(wait, 600))
        results = self.store.query("SELECT node, status, execution_time AS executionTime, message, "
                                   "rows_affected AS rowsAffected FROM node_results WHERE run = ? ORDER BY node",
                                   run_id)
        return {**run, "results": results}

    async def run_logs(self, caller, run_id, tail=400):
        await self.run_for(caller, run_id)
        return {"run": run_id, "lines": await self.runs.logs(run_id, max(1, min(tail, 5000)))}

    async def list_runs(self, caller, project_id, environment=None, limit=30):
        await self.project_for(caller, project_id)
        return self.store.runs(project_id, environment, max(1, min(limit, 200)))

    async def cancel_run(self, caller, run_id):
        run = await self.run_for(caller, run_id)
        await self.project_for(caller, run["project"], "writer")
        if run["status"] in ("queued", "running"):
            await self.runs.cancel(run_id)
        return {"cancelled": run["status"] in ("queued", "running"), "run": run_id}

    async def compile(self, caller, project_id, environment="development", ref="main", select="", sql=""):
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
                "output": "\n".join(lines[-200:]), "error": run["error"]}

    async def preview(self, caller, project_id, model, environment="development", ref="main", limit=20):
        if not re.match(r"^[A-Za-z0-9_.]{1,200}$", model or ""):
            raise DbtError(422, "Name one model.", "invalid_selector")
        run = await self.start_run(caller, project_id, "show", environment, ref, model,
                                   extra=["--limit", str(max(1, min(limit, 100))), "--output", "json"])
        run = await self.runs.wait(run["id"], 300)
        path = self.runs.artifacts / run["id"] / "out" / "show.json"
        rows = json.loads(path.read_text()) if path.exists() else None
        return {"run": run["id"], "status": run["status"], "rows": rows,
                "output": None if rows is not None else "\n".join((await self.runs.logs(run["id"], 60))[-60:])}

    # Schedules

    async def schedule_scope(self, caller, project_id, environment):
        return await self.project_for(caller, project_id, "writer" if environment == "development" else "admin")

    async def list_schedules(self, caller, project_id):
        await self.project_for(caller, project_id)
        return self.store.schedules(project_id)

    async def set_schedule(self, caller, project_id, environment, cron, command="build", select="", ref="main",
                           enabled=True, schedule_id=None):
        if environment not in ENVIRONMENTS or command not in ("build", "run", "seed", "test"):
            raise DbtError(422, "Schedule build, run, seed or test in an environment.", "invalid_request")
        if not croniter.is_valid(cron or "") or len(cron.split()) != 5:
            raise DbtError(422, "Use a five-field cron expression in UTC, such as '0 6 * * *'.", "invalid_cron")
        if not SELECTOR.match(select or ""):
            raise DbtError(422, "The selector contains unsupported characters.", "invalid_selector")
        _, me, _ = await self.schedule_scope(caller, project_id, environment)
        if environment != "development" and ref != "main":
            raise DbtError(403, "Acceptance and production schedules run main.", "main_required")
        await asyncio.to_thread(self.repos.resolve, project_id, ref)
        next_run = croniter(cron, time.time()).get_next(float)
        fields = {"environment": environment, "cron": cron, "command": command, "selector": select, "ref": ref,
                  "enabled": int(bool(enabled)), "next_run_at": next_run}
        if schedule_id:
            existing = self.store.schedule(schedule_id)
            if not existing or existing["project"] != project_id:
                raise DbtError(404, "No such schedule.", "unknown_schedule")
            await self.schedule_scope(caller, project_id, existing["environment"])
            return self.store.update_schedule(schedule_id, **fields)
        return self.store.create_schedule(project=project_id, created_by=me["user"]["name"], **fields)

    async def delete_schedule(self, caller, schedule_id):
        schedule = self.store.schedule(schedule_id)
        if not schedule:
            raise DbtError(404, "No such schedule.", "unknown_schedule")
        await self.schedule_scope(caller, schedule["project"], schedule["environment"])
        self.store.delete_schedule(schedule_id)
        return {"deleted": True, "schedule": schedule_id}

    # Pipelines

    def latest_structure(self, project_id, environment):
        for run in self.store.runs(project_id, environment, 200):
            if run["status"] in ("success", "failed") and (run["summary"] or {}).get("infoSchema"):
                info = self.runs.artifacts / run["id"] / "out" / "target" / "info_schema" / "v1"
                if Path(info).exists():
                    return run, self.graphs.get(run["id"], info)
        return None, None

    async def pipeline(self, caller, project_id, environment="development", node=None):
        await self.project_for(caller, project_id)
        run, structure = await asyncio.to_thread(self.latest_structure, project_id, environment)
        runs = self.store.runs(project_id, environment, 20)
        if not structure:
            return {"project": project_id, "environment": environment, "nodes": [], "edges": [], "structureRun": None,
                    "runs": runs, "hint": "Run build, compile or parse in this environment to draw the pipeline."}
        latest = self.store.latest_nodes(project_id, environment)
        result = lineage.graph(structure, latest, catalogs=run["summary"].get("catalogs", {}), detail=bool(node))
        if node:
            result["nodes"] = [n for n in result["nodes"] if n["id"] == node]
            if not result["nodes"]:
                raise DbtError(404, "No such node in this pipeline.", "unknown_node")
        return {"project": project_id, "environment": environment, "structureRun": run["id"],
                "revision": run["revision"], "runs": runs, **result}

    async def model(self, caller, project_id, node, environment="development"):
        pipeline = await self.pipeline(caller, project_id, environment, node)
        return {**pipeline["nodes"][0], "environment": environment, "structureRun": pipeline["structureRun"]}

    async def lineage(self, caller, project_id, node, environment="development", depth=3):
        await self.project_for(caller, project_id)
        run, structure = await asyncio.to_thread(self.latest_structure, project_id, environment)
        if not structure:
            raise DbtError(404, "No pipeline yet: run build, compile or parse first.", "no_pipeline")
        return {"environment": environment, "structureRun": run["id"],
                **lineage.neighbours(structure, node, max(1, min(depth, 20)))}

    def docs_path(self, run_id):
        return self.runs.artifacts / run_id / "out" / "docs"
