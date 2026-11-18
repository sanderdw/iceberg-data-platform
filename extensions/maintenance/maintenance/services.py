"""Every capability of the Maintenance extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the database's team | Allowed |
| --- | --- |
| reader | View table health, policies, runs and logs |
| writer | Also refresh table health, dry-run a policy, run maintenance now and stop runs |
| admin, bucket-admin | Also set and remove policies and schedules, enable environments |

Runs always execute as the team's automation principal for the database's environment, never as the
person or agent that starts them.
"""

import time

from croniter import croniter

from . import policy as policies
from .config import ENVIRONMENTS
from .errors import MaintenanceError

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
MODES = {"inspect": ("inspect", False), "dry-run": ("maintain", True), "maintain": ("maintain", False)}
NOTE = ("Expired snapshots can no longer be used for time travel. Files they leave behind are deleted by orphan "
        "cleanup in a later run, never in the same run. Branch and tag heads and each ref's own retention are "
        "always kept.")


def attention(health, policy, excluded):
    """Why a table needs maintenance, from its last known health: a list of short reasons."""
    if not health:
        return []
    reasons = []
    compaction = policy["compaction"]
    if compaction["enabled"] and health.get("compactableFiles", 0) >= compaction["minInputFiles"]:
        reasons.append(f"{health['compactableFiles']} files to compact")
    if policy["expireSnapshots"]["enabled"] and health.get("expirableSnapshots", 0) > 0:
        reasons.append(f"{health['expirableSnapshots']} snapshots to expire")
    deletes = health.get("positionDeleteFiles", 0) + health.get("equalityDeleteFiles", 0)
    if deletes >= 5:
        reasons.append(f"{deletes} delete files")
    manifests = policy["rewriteManifests"]
    if manifests["enabled"] and health.get("manifests", 0) > manifests["minManifests"]:
        reasons.append(f"{health['manifests']} manifests")
    return [] if excluded else reasons


def state(row, policy_row, reasons):
    if row["last_status"] == "failed":
        return "failed"
    if policy_row is None:
        return "no-policy"
    if row["name"] in policy_row["exclude"]:
        return "excluded"
    return "attention" if reasons else "healthy"


class Services:
    def __init__(self, settings, store, bridge, runs):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.runs = runs

    # Context and authorization

    async def me(self, caller):
        return await self.bridge.me(caller.token)

    @staticmethod
    def role(me, team):
        return next((m["role"] for m in me["memberships"] if m["team"] == team), None)

    @staticmethod
    def require(role, minimum, what="the database's team"):
        if minimum == "writer" and role not in WRITERS:
            raise MaintenanceError(403, f"This needs the writer or administrator role in {what}.", "forbidden_role")
        if minimum == "admin" and role not in ADMINS:
            raise MaintenanceError(403, f"This needs the administrator role in {what}.", "forbidden_role")

    async def database_for(self, caller, database_id, minimum="reader"):
        me = await self.me(caller)
        database = next((d for d in me["databases"] if d["id"] == database_id), None)
        role = self.role(me, database["team"]) if database else None
        if not database or not role:
            raise MaintenanceError(404, "No such database in your teams.", "unknown_database")
        self.require(role, minimum)
        return database, me, role

    async def run_for(self, caller, run_id, minimum="reader"):
        run = self.store.run(run_id)
        if not run:
            raise MaintenanceError(404, "No such run in your teams.", "unknown_run")
        try:
            database, me, role = await self.database_for(caller, run["database"], minimum)
        except MaintenanceError as exc:
            if exc.code == "unknown_database":
                raise MaintenanceError(404, "No such run in your teams.", "unknown_run") from None
            raise
        return run, database, me, role

    @staticmethod
    def triggered(caller, me):
        return me["user"]["name"], caller.client if caller.agent else ""

    @staticmethod
    def team_name(me, team):
        return next((m["teamName"] for m in me["memberships"] if m["team"] == team), team)

    # Presentation

    @staticmethod
    def effective(policy_row):
        return policy_row["settings"] if policy_row else policies.defaults()

    def public_policy(self, database, policy_row, role=None):
        if not policy_row:
            return {"database": database["id"], "databaseName": database["name"], "team": database["team"],
                    "environment": database["environment"], "exists": False, "settings": policies.defaults(),
                    "exclude": [], "schedule": None, "revision": 0, "role": role,
                    "note": "No policy yet: the defaults show what a policy would start with."}
        return {
            "database": database["id"], "databaseName": database["name"], "team": database["team"],
            "environment": database["environment"], "exists": True, "settings": policy_row["settings"],
            "exclude": policy_row["exclude"], "schedule": self.public_schedule(policy_row),
            "revision": policy_row["revision"], "createdAt": policy_row["created_at"],
            "createdBy": policy_row["created_by"], "updatedAt": policy_row["updated_at"],
            "updatedBy": policy_row["updated_by"], "role": role, "note": NOTE,
        }

    @staticmethod
    def public_schedule(policy_row):
        if not policy_row or not policy_row["cron"]:
            return None
        return {"cron": policy_row["cron"], "enabled": bool(policy_row["schedule_enabled"]),
                "nextRunAt": policy_row["next_run_at"] if policy_row["schedule_enabled"] else None}

    @staticmethod
    def public_run(run):
        summary = {k: v for k, v in run["summary"].items() if k not in ("principal", "tables")}
        return {
            "id": run["id"], "database": run["database"], "team": run["team"], "environment": run["environment"],
            "mode": "inspect" if run["kind"] == "inspect" else "dry-run" if run["dry_run"] else "maintain",
            "tables": run["tables"], "status": run["status"], "triggeredBy": run["triggered_by"],
            "agent": run["agent"] or None, "schedule": bool(run["schedule"]), "createdAt": run["created_at"],
            "startedAt": run["started_at"], "finishedAt": run["finished_at"], "exitCode": run["exit_code"],
            "summary": summary, "error": run["error"] or None,
        }

    def public_table(self, row, database, policy_row):
        settings = self.effective(policy_row)
        excluded = bool(policy_row and row["name"] in policy_row["exclude"])
        reasons = attention(row["health"], settings, excluded)
        return {"database": row["database"], "databaseName": database["name"], "team": database["team"],
                "environment": database["environment"], "name": row["name"],
                "state": state(row, policy_row, reasons), "reasons": reasons, "health": row["health"],
                "inspectedAt": row["inspected_at"], "inspectedBy": row["inspected_by"], "lastRun": row["last_run"],
                "lastStatus": row["last_status"], "lastError": row["last_error"] or None,
                "lastActions": row["last_actions"], "lastMaintainedAt": row["last_maintained_at"]}

    # Discovery

    async def enabled(self, caller, teams):
        found = {}
        for team in teams:
            envs = {p["environment"] for p in await self.bridge.team_automation(caller.token, team)}
            found[team] = [env for env in ENVIRONMENTS if env in envs]
        return found

    async def whoami(self, caller):
        me = await self.me(caller)
        discovery = await self.bridge.discovery()
        enabled = await self.enabled(caller, [m["team"] for m in me["memberships"]])
        return {**me, "extension": self.settings.extension_id, "origin": self.settings.origin,
                "environments": list(ENVIRONMENTS), "operations": policies.LABELS,
                "defaults": policies.defaults(),
                "enabledEnvironments": [{"team": team, "environments": envs} for team, envs in enabled.items()],
                "userPortalUrl": discovery.get("userPortalUrl", "")}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise MaintenanceError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return [{"environment": env, "enabled": env in enabled,
                 "databases": [{"id": d["id"], "name": d["name"]} for d in me["databases"]
                               if d["team"] == team and d["environment"] == env],
                 **({"principal": enabled[env]["id"], "enabledBy": enabled[env].get("createdBy")}
                    if env in enabled else {})}
                for env in ENVIRONMENTS]

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise MaintenanceError(422, "Choose development, acceptance or production.", "invalid_request")
        return await self.bridge.enable(caller.token, team, environment)

    async def disable_environment(self, caller, team, environment):
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise MaintenanceError(404, "Maintenance is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return {"disabled": True, "environment": environment}

    # Databases and tables

    def visible(self, me, team=None, environment=None):
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        return [d for d in me["databases"] if d["team"] in roles and team in (None, d["team"])
                and environment in (None, d["environment"])], roles

    async def list_databases(self, caller, team=None, environment=None):
        me = await self.me(caller)
        databases, roles = self.visible(me, team, environment)
        ids = [d["id"] for d in databases]
        policy_rows, last = self.store.policies(ids), self.store.last_runs(ids)
        tables = {}
        for row in self.store.tables(ids):
            tables.setdefault(row["database"], []).append(row)
        result = []
        for d in databases:
            policy_row = policy_rows.get(d["id"])
            rows = [self.public_table(r, d, policy_row) for r in tables.get(d["id"], [])]
            result.append({
                "id": d["id"], "name": d["name"], "team": d["team"], "teamName": self.team_name(me, d["team"]),
                "environment": d["environment"], "role": roles[d["team"]], "policy": bool(policy_row),
                "schedule": self.public_schedule(policy_row), "tables": len(rows),
                "attention": sum(r["state"] in ("attention", "failed") for r in rows),
                "inspected": bool(rows), "lastRun": self.public_run(last[d["id"]]) if d["id"] in last else None})
        return result

    async def list_tables(self, caller, database=None, team=None, environment=None, attention_only=False):
        me = await self.me(caller)
        databases, _ = self.visible(me, team, environment)
        if database:
            databases = [d for d in databases if d["id"] == database]
            if not databases:
                raise MaintenanceError(404, "No such database in your teams.", "unknown_database")
        by_id = {d["id"]: d for d in databases}
        policy_rows = self.store.policies(list(by_id))
        rows = [self.public_table(r, by_id[r["database"]], policy_rows.get(r["database"]))
                for r in self.store.tables(list(by_id))]
        if attention_only:
            rows = [r for r in rows if r["state"] in ("attention", "failed")]
        return rows

    async def get_table(self, caller, database_id, name):
        database, _, role = await self.database_for(caller, database_id)
        row = self.store.table(database_id, name)
        if not row:
            raise MaintenanceError(404, "No such table in this database's last inspection. Refresh its health.",
                                   "unknown_table")
        policy_row = self.store.policy(database_id)
        runs = [self.public_run(r) for r in self.store.runs(database_id, 20)
                if not r["tables"] or name in r["tables"]][:10]
        return {**self.public_table(row, database, policy_row), "role": role,
                "policy": self.public_policy(database, policy_row, role), "recentRuns": runs}

    # Policies

    async def get_policy(self, caller, database_id):
        database, _, role = await self.database_for(caller, database_id)
        return self.public_policy(database, self.store.policy(database_id), role)

    async def set_policy(self, caller, database_id, data):
        database, me, role = await self.database_for(caller, database_id, "admin")
        existing = self.store.policy(database_id)
        settings = policies.merge(data.get("settings") or {}, existing["settings"] if existing else None)
        exclude = policies.excluded(data["exclude"]) if data.get("exclude") is not None else (
            existing["exclude"] if existing else [])
        cron = existing["cron"] if existing else ""
        enabled = bool(existing["schedule_enabled"]) if existing else False
        if "cron" in data:
            cron = (data["cron"] or "").strip()
            enabled = bool(cron)
        if data.get("scheduleEnabled") is not None:
            enabled = bool(data["scheduleEnabled"]) and bool(cron)
        if cron:
            self.check_cron(cron)
        next_run = croniter(cron, time.time()).get_next(float) if cron and enabled else None
        saved = self.store.save_policy(database_id, data.get("expected_revision"), team=database["team"],
                                       environment=database["environment"], settings=settings, exclude=exclude,
                                       cron=cron, schedule_enabled=int(enabled), next_run_at=next_run,
                                       updated_by=me["user"]["name"])
        if saved is False:
            raise MaintenanceError(409, "The policy changed since you read it. Read it again.", "revision_conflict")
        return self.public_policy(database, saved, role)

    async def delete_policy(self, caller, database_id):
        await self.database_for(caller, database_id, "admin")
        if not self.store.delete_policy(database_id):
            raise MaintenanceError(404, "This database has no policy.", "no_policy")
        return {"deleted": True, "database": database_id,
                "note": "Scheduled maintenance stops. Table health and past runs stay."}

    @staticmethod
    def check_cron(cron):
        if not croniter.is_valid(cron) or len(cron.split()) != 5:
            raise MaintenanceError(422, "Give a five-field cron expression in UTC, such as '0 2 * * *'.",
                                   "invalid_cron")
        times = croniter(cron, time.time())
        first, second = times.get_next(float), times.get_next(float)
        if second - first < 3600:
            raise MaintenanceError(422, "Maintenance runs at most once an hour.", "invalid_cron")

    # Runs

    async def run_maintenance(self, caller, database_id, mode="inspect", tables=None, wait=0):
        if mode not in MODES:
            raise MaintenanceError(422, "mode is inspect, dry-run or maintain.", "invalid_request")
        database, me, _ = await self.database_for(caller, database_id, "writer")
        policy_row = self.store.policy(database_id)
        if mode == "maintain" and not policy_row:
            raise MaintenanceError(409, "Set a policy for this database first. A dry run shows what the defaults "
                                        "would do.", "no_policy")
        triggered_by, agent = self.triggered(caller, me)
        run = await self.start_run(database, mode, tables or [], policy_row, triggered_by=triggered_by, agent=agent)
        if wait:
            run = await self.runs.wait(run["id"], wait)
        return self.public_run(run)

    async def start_run(self, database, mode, tables, policy_row, *, triggered_by, agent="", schedule=""):
        if self.store.active_run(database["id"]):
            raise MaintenanceError(409, "This database already has a run in progress.", "run_in_progress")
        if len(tables) > 200 or any(not isinstance(t, str) or len(t) > 300 or "." not in t for t in tables):
            raise MaintenanceError(422, "Name at most 200 tables, as namespace.table.", "invalid_request")
        kind, dry_run = MODES[mode]
        principal = await self.bridge.principal_for(database["team"], database["environment"])
        return self.runs.start(database=database["id"], team=database["team"], environment=database["environment"],
                               kind=kind, dry_run=dry_run, tables=sorted(set(tables)),
                               settings=self.effective(policy_row),
                               exclude=policy_row["exclude"] if policy_row else [], principal=principal,
                               triggered_by=triggered_by, agent=agent, schedule=schedule)

    async def list_runs(self, caller, database_id, limit=20):
        await self.database_for(caller, database_id)
        return [self.public_run(r) for r in self.store.runs(database_id, limit)]

    async def get_run(self, caller, run_id):
        run, database, _, role = await self.run_for(caller, run_id)
        return {**self.public_run(run), "databaseName": database["name"], "role": role,
                "results": run["summary"].get("tables", [])}

    async def get_run_logs(self, caller, run_id, tail=500):
        run, _, _, _ = await self.run_for(caller, run_id)
        return {"run": run_id, "status": run["status"], "lines": await self.runs.logs(run_id, tail)}

    async def cancel_run(self, caller, run_id):
        run, _, _, _ = await self.run_for(caller, run_id, "writer")
        if run["status"] not in ("queued", "running"):
            raise MaintenanceError(409, "The run has already finished.", "run_finished")
        await self.runs.cancel(run_id)
        return {"cancelling": True, "run": run_id}
