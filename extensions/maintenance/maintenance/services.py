"""Every capability of the Maintenance extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the database's team | Allowed |
| --- | --- |
| reader | View table health, policies, runs and logs |
| writer | Also refresh table health, dry-run a policy, run maintenance now and stop runs |
| admin, bucket-admin | Also set and remove policies, exclusions and schedules, enable environments |

A database's policies are Apache Polaris policies on its catalog (`policy.py`). Maintenance reads and writes them
with the tokens of the team's automation principal for the database's environment, so it needs Maintenance to be
enabled there. Runs always execute as that principal, never as the person or agent that starts them.

Every response is built here, field by field (the `public_*` functions): no stored row, no Polaris policy and no
Bridge answer is passed on as it is. Times leave through `timestamp()`.
"""

import asyncio
import time
from datetime import UTC, datetime

from croniter import croniter

from . import policy as policies
from .config import ENVIRONMENTS
from .errors import MaintenanceError

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
MODES = {"inspect": ("inspect", False), "dry-run": ("maintain", True), "maintain": ("maintain", False)}
ACTIVE = ("queued", "running")
NOTE = ("Expired snapshots can no longer be used for time travel. Files they leave behind are deleted by orphan "
        "cleanup in a later run, never in the same run. Branch and tag heads and each ref's own retention are "
        "always kept.")
NO_POLICY = ("No policy yet: the defaults show what a policy would start with. Saving creates one Apache Polaris "
             "policy per type and attaches it to the database's catalog.")


def timestamp(value):
    """A stored time (seconds since 1970) as RFC 3339 in UTC with milliseconds, such as
    2026-11-24T09:15:02.123Z. None stays None."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def applied_policies(stored):
    """The policies a table's last run resolved, per type; complete also for a table never resolved."""
    result = {}
    for key in policies.TYPES:
        entry = (stored or {}).get(key) or {}
        result[key] = {"settings": entry.get("settings") or {**policies.defaults()[key], "enabled": False},
                       "policy": entry.get("policy"), "problem": entry.get("problem")}
    return result


def attention(health, applied):
    """Why a table needs maintenance, from its last known health and the policies that applied: short reasons.
    A table that no policy applies to is judged by the defaults."""
    if not health:
        return []
    if any(p["policy"] for p in applied.values()):
        settings = {key: p["settings"] for key, p in applied.items()}
    else:
        settings = policies.defaults()
    if not any(s["enabled"] for s in settings.values()):
        return []
    reasons = []
    compaction = settings["dataCompaction"]
    if compaction["enabled"] and health.get("compactableFiles", 0) >= compaction["minInputFiles"]:
        reasons.append(f"{health['compactableFiles']} files to compact")
    if settings["snapshotExpiry"]["enabled"] and health.get("expirableSnapshots", 0) > 0:
        reasons.append(f"{health['expirableSnapshots']} snapshots to expire")
    deletes = health.get("positionDeleteFiles", 0) + health.get("equalityDeleteFiles", 0)
    if deletes >= 5:
        reasons.append(f"{deletes} delete files")
    manifests = settings["metadataCompaction"]
    if manifests["enabled"] and health.get("manifests", 0) > manifests["minCountToMerge"]:
        reasons.append(f"{health['manifests']} manifests")
    return reasons


def state(row, applied, reasons):
    if row["last_status"] == "failed":
        return "failed"
    found = [p for p in applied.values() if p["policy"]]
    if not found:
        return "no-policy"
    # Off everywhere, and switched off below the catalog: excluded.
    if not any(p["settings"]["enabled"] for p in applied.values()) and any(
            p["policy"]["appliedAt"] != "catalog" for p in found):
        return "excluded"
    return "attention" if reasons else "healthy"


def apps(discovery):
    """The Apps menu's entries (Bridge capability `app-directory`); with an older core, just the user portal."""
    pages = discovery.get("userPortalPages") or (
        [{"id": "portal", "title": "User portal", "url": discovery["userPortalUrl"]}]
        if discovery.get("userPortalUrl") else [])
    return {"workspace": [{k: str(p.get(k, "")) for k in ("id", "title", "url")} for p in pages],
            "extensions": [{k: str(e.get(k) or "") for k in ("id", "title", "purpose", "origin")}
                           for e in discovery.get("extensions") or []]}


class Services:
    def __init__(self, settings, store, bridge, runs, polaris):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.runs = runs
        self.polaris = polaris

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

    async def catalog_for(self, database, access="read"):
        """The database's catalog as the team's automation principal; 409 environment_not_enabled without one."""
        principal = await self.bridge.principal_for(database["team"], database["environment"])
        return self.polaris.catalog(database["id"], principal=principal["id"], access=access)

    @staticmethod
    def triggered(caller, me):
        return me["user"]["name"], caller.client if caller.agent else ""

    @staticmethod
    def team_name(me, team):
        return next((m["teamName"] for m in me["memberships"] if m["team"] == team), team)

    # Presentation

    @staticmethod
    def public_policy_ref(entry, problem):
        return {"name": str(entry.get("name", "")), "namespace": ".".join(entry.get("namespace") or []),
                "version": int(entry.get("version") or 0), "managed": entry.get("namespace") == [policies.HOME],
                "problem": problem}

    @staticmethod
    def public_exclusion(row):
        return {"target": row["target"], "level": row["level"], "types": list(row["types"]),
                "createdBy": row["created_by"], "createdAt": timestamp(row["created_at"])}

    def public_policy(self, database, attached, exclusions, schedule, role, last_scheduled=None):
        settings, refs = {}, {}
        for key in policies.TYPES:
            entry = attached.get(key)
            if entry is None:
                settings[key], refs[key] = policies.defaults()[key], None
                continue
            settings[key], problem = policies.from_content(key, entry.get("content"))
            refs[key] = self.public_policy_ref(entry, problem)
        exists = bool(attached)
        return {
            "database": database["id"], "databaseName": database["name"], "team": database["team"],
            "environment": database["environment"], "exists": exists, "settings": settings, "policies": refs,
            "exclusions": [self.public_exclusion(r) for r in exclusions],
            "schedule": self.public_schedule(schedule, last_scheduled), "role": role,
            "note": NOTE if exists else NO_POLICY,
        }

    @staticmethod
    def public_schedule(row, last_scheduled=None):
        """A database's schedule; None when it has none."""
        if not row:
            return None
        enabled = bool(row["enabled"])
        return {"cron": row["cron"], "enabled": enabled,
                "nextRunAt": timestamp(row["next_run_at"]) if enabled else None, "lastRun": last_scheduled or None}

    @staticmethod
    def public_run(run):
        """A run as a list shows it: without each table's results."""
        summary = {k: v for k, v in run["summary"].items() if k not in ("principal", "tables")}
        return {
            "id": run["id"], "database": run["database"], "team": run["team"], "environment": run["environment"],
            "mode": "inspect" if run["kind"] == "inspect" else "dry-run" if run["dry_run"] else "maintain",
            "tables": run["tables"], "status": run["status"], "triggeredBy": run["triggered_by"],
            "agent": run["agent"] or None, "scheduled": bool(run["schedule"]),
            "createdAt": timestamp(run["created_at"]), "startedAt": timestamp(run["started_at"]),
            "finishedAt": timestamp(run["finished_at"]), "exitCode": run["exit_code"], "summary": summary,
            "error": run["error"] or None,
        }

    def public_run_detail(self, run, database, role):
        """A run as get and start return it: with the database's name, your role and each table's results."""
        return {**self.public_run(run), "databaseName": database["name"], "role": role,
                "results": run["summary"].get("tables") or []}

    @staticmethod
    def public_table(row, database):
        applied = applied_policies(row["policies"])
        reasons = attention(row["health"], applied)
        return {"database": row["database"], "databaseName": database["name"], "team": database["team"],
                "environment": database["environment"], "name": row["name"],
                "state": state(row, applied, reasons), "reasons": reasons, "health": row["health"],
                "policies": applied,
                "inspectedAt": timestamp(row["inspected_at"]), "inspectedBy": row["inspected_by"],
                "lastRun": row["last_run"] or None, "lastStatus": row["last_status"],
                "lastError": row["last_error"] or None, "lastActions": row["last_actions"],
                "lastMaintainedAt": timestamp(row["last_maintained_at"])}

    @staticmethod
    def public_environment(me, team, environment, principal):
        """One environment of a team: whether Maintenance is enabled there, by whom, and its databases."""
        return {"team": team, "environment": environment, "enabled": principal is not None,
                "enabledBy": (principal.get("createdBy") or None) if principal else None,
                "databases": [{"id": d["id"], "name": d["name"]} for d in me["databases"]
                              if d["team"] == team and d["environment"] == environment]}

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
        return {
            "user": {"id": me["user"]["id"], "name": me["user"]["name"]},
            "memberships": [{"team": m["team"], "teamName": m["teamName"], "role": m["role"]}
                            for m in me["memberships"]],
            "databases": [{"id": d["id"], "name": d["name"], "team": d["team"], "environment": d["environment"],
                           "status": d.get("status", "")} for d in me["databases"]],
            "extension": self.settings.extension_id, "origin": self.settings.origin,
            "environments": list(ENVIRONMENTS),
            "enabledEnvironments": [{"team": team, "environments": envs} for team, envs in enabled.items()],
            "userPortalUrl": discovery.get("userPortalUrl", ""), "apps": apps(discovery),
            "policyTypes": {key: {"type": ptype, "title": policies.TITLES[key]}
                            for key, ptype in policies.TYPES.items()},
            "defaults": policies.defaults(),
        }

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise MaintenanceError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return {"environments": [self.public_environment(me, team, env, enabled.get(env)) for env in ENVIRONMENTS]}

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise MaintenanceError(422, "Choose development, acceptance or production.", "invalid_request")
        me = await self.me(caller)
        principal = await self.bridge.enable(caller.token, team, environment)
        return self.public_environment(me, team, environment, principal)

    async def disable_environment(self, caller, team, environment):
        me = await self.me(caller)
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise MaintenanceError(409, "Maintenance is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return self.public_environment(me, team, environment, None)

    # Databases and tables

    def visible(self, me, team=None, environment=None):
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        if team and team not in roles:
            raise MaintenanceError(403, "You are not a member of this team.", "not_a_member")
        return [d for d in me["databases"] if d["team"] in roles and team in (None, d["team"])
                and environment in (None, d["environment"])], roles

    async def has_policy(self, database):
        """Whether the database's catalog has maintenance policies; None when Maintenance cannot read it."""
        try:
            catalog = await self.catalog_for(database)
            return bool(await catalog.catalog_policies())
        except MaintenanceError:
            return None

    async def list_databases(self, caller, team=None, environment=None):
        me = await self.me(caller)
        databases, roles = self.visible(me, team, environment)
        ids = [d["id"] for d in databases]
        last, scheduled = self.store.last_runs(ids), self.store.last_scheduled_runs(ids)
        schedules = self.store.schedules(ids)
        found = await asyncio.gather(*(self.has_policy(d) for d in databases))
        tables = {}
        for row in self.store.tables(ids):
            tables.setdefault(row["database"], []).append(row)
        result = []
        for d, policy in zip(databases, found, strict=True):
            rows = [self.public_table(r, d) for r in tables.get(d["id"], [])]
            result.append({
                "id": d["id"], "name": d["name"], "team": d["team"], "teamName": self.team_name(me, d["team"]),
                "environment": d["environment"], "role": roles[d["team"]], "policy": policy,
                "schedule": self.public_schedule(schedules.get(d["id"]), scheduled.get(d["id"])), "tables": len(rows),
                "attention": sum(r["state"] in ("attention", "failed") for r in rows),
                "inspected": bool(rows), "lastRun": self.public_run(last[d["id"]]) if d["id"] in last else None})
        return {"databases": result}

    async def list_table_health(self, caller, database=None, team=None, environment=None, attention_only=False,
                                limit=500):
        """The tables' health, at most `limit` of them; `total` counts the ones that match before the limit."""
        me = await self.me(caller)
        databases, _ = self.visible(me, team, environment)
        if database:
            databases = [d for d in databases if d["id"] == database]
            if not databases:
                raise MaintenanceError(404, "No such database in your teams.", "unknown_database")
        by_id = {d["id"]: d for d in databases}
        rows = [self.public_table(r, by_id[r["database"]]) for r in self.store.tables(list(by_id))]
        if attention_only:
            rows = [r for r in rows if r["state"] in ("attention", "failed")]
        return {"tables": rows[:limit], "total": len(rows)}

    async def get_table_health(self, caller, database_id, name):
        database, _, role = await self.database_for(caller, database_id)
        row = self.store.table(database_id, name)
        if not row:
            raise MaintenanceError(404, "No such table in this database's last inspection. Refresh its health.",
                                   "unknown_table")
        runs = [self.public_run(r) for r in self.store.runs(database_id, 20)
                if not r["tables"] or name in r["tables"]][:10]
        return {**self.public_table(row, database), "role": role, "recentRuns": runs}

    # Policies, exclusions and the schedule

    def policy_view(self, database, attached, role):
        last = self.store.last_scheduled_runs([database["id"]]).get(database["id"])
        return self.public_policy(database, attached, self.store.exclusions(database["id"]),
                                  self.store.schedule(database["id"]), role, last)

    async def get_policy(self, caller, database_id):
        database, _, role = await self.database_for(caller, database_id)
        catalog = await self.catalog_for(database)
        return self.policy_view(database, await catalog.catalog_policies(fresh=True), role)

    async def update_policy(self, caller, database_id, data):
        """Change what `data` names and keep the rest. Each type is one Polaris policy attached to the catalog:
        an existing one is updated at the version it was read at, a missing one is created in Maintenance's
        namespace from the defaults. `exclusions` replaces the excluded namespaces and tables. The schedule is
        not part of it: set_schedule and delete_schedule."""
        database, me, role = await self.database_for(caller, database_id, "admin")
        catalog = await self.catalog_for(database, "write")
        attached = await catalog.catalog_policies(fresh=True)
        expected = data.get("expectedVersions") or {}
        for key, version in expected.items():
            if key not in policies.TYPES:
                raise MaintenanceError(422, f"Unknown policy type {key}.", "invalid_policy")
            have = attached[key].get("version") if key in attached else None
            if have != version:
                raise MaintenanceError(409, f"The {key} policy changed since you read it. Read it again.",
                                       "revision_conflict")
        base = {key: policies.from_content(key, attached[key].get("content"))[0] if key in attached
                else policies.defaults()[key] for key in policies.TYPES}
        named = data.get("settings") or {}
        merged = policies.merge(named, base)
        exclusions = policies.exclusions(data["exclusions"]) if data.get("exclusions") is not None else None
        try:
            # New exclusions first and removed ones last: a request that fails part way never leaves the
            # database's policies in force on a namespace or table that was meant to be excluded.
            if exclusions is not None:
                await self.add_exclusions(catalog, database_id, exclusions, me["user"]["name"])
            for key, ptype in policies.TYPES.items():
                # A type that exists and is not named stays as it is, whoever wrote it.
                if key in attached and key not in named:
                    continue
                await self.write_type(catalog, key, ptype, merged[key], attached.get(key))
            if exclusions is not None:
                await self.remove_exclusions(catalog, database_id, exclusions, me["user"]["name"])
        finally:
            self.polaris.changed(database_id)
        return self.policy_view(database, await catalog.catalog_policies(fresh=True), role)

    @staticmethod
    async def write_type(catalog, key, ptype, settings, entry):
        if entry is not None:
            content = policies.to_content(key, settings, entry.get("content"))
            if content != policies.parse(entry.get("content")):
                await catalog.update_policy(entry["namespace"], entry["name"], content, entry.get("version") or 0)
            return
        name = policies.CATALOG_NAMES[key]
        content = policies.to_content(key, settings)
        created = await catalog.create_policy(name, ptype, content, f"Maintenance: {policies.TITLES[key]}, the "
                                                                    "database's own policy.")
        if created is None:
            # Ours, but detached by someone: bring it up to date and attach it again.
            existing = await catalog.load_policy([policies.HOME], name)
            await catalog.update_policy([policies.HOME], name, policies.to_content(key, settings, existing["content"]),
                                        existing["version"])
        if await catalog.attach([policies.HOME], name, {"type": "catalog"}) == 409:
            raise MaintenanceError(409, f"Another {ptype} policy was attached to the database meanwhile. Read the "
                                        "policy again.", "revision_conflict")

    @staticmethod
    async def ensure_excluded(catalog, key, ready):
        """The shared disabled policy of a type. One of that name made or changed elsewhere is set back to off
        before it is attached: an exclusion must never turn maintenance on."""
        if key in ready:
            return
        name = policies.EXCLUDED_NAMES[key]
        created = await catalog.create_policy(name, policies.TYPES[key], policies.disabled_content(),
                                              f"Maintenance: {policies.TITLES[key]} off, for excluded namespaces "
                                              "and tables.")
        if created is None:
            existing = await catalog.load_policy([policies.HOME], name)
            if existing is None or existing.get("policy-type") != policies.TYPES[key]:
                raise MaintenanceError(409, f"{policies.HOME}.{name} exists but is not a {policies.TYPES[key]} "
                                            "policy. Remove it in Polaris first.", "exclusion_conflict")
            if policies.parse(existing.get("content")) != policies.disabled_content():
                await catalog.update_policy([policies.HOME], name, policies.disabled_content(),
                                            existing.get("version") or 0)
        ready.add(key)

    async def remove_exclusions(self, catalog, database_id, wanted, by):
        """Detach the disabled policies of what is no longer excluded."""
        current = {(r["level"], r["target"]): set(r["types"]) for r in self.store.exclusions(database_id)}
        desired = {(e["level"], e["target"]): set(e["types"]) for e in wanted}
        for (level, target), types in current.items():
            keep = desired.get((level, target), set())
            for key in types - keep:
                await catalog.detach([policies.HOME], policies.EXCLUDED_NAMES[key], policies.target_of(level, target))
            if types - keep:
                self.store.set_exclusion(database_id, level, target, [t for t in policies.TYPES if t in types & keep],
                                         by)

    async def add_exclusions(self, catalog, database_id, wanted, by):
        """Attach the disabled policy of each excluded type to each target. Polaris then resolves the override
        itself: a table's policy wins over its namespace's, a namespace's over the catalog's."""
        current = {(r["level"], r["target"]): set(r["types"]) for r in self.store.exclusions(database_id)}
        desired = {(e["level"], e["target"]): set(e["types"]) for e in wanted}
        ready = set()
        for (level, target), types in desired.items():
            # Everything attached stays recorded until remove_exclusions detaches it.
            have = current.get((level, target), set())
            for key in [t for t in policies.TYPES if t in types - have]:
                await self.ensure_excluded(catalog, key, ready)
                status = await catalog.attach([policies.HOME], policies.EXCLUDED_NAMES[key],
                                              policies.target_of(level, target))
                if status == 404:
                    raise MaintenanceError(422, f"There is no {level} {target} in this database.",
                                           "invalid_exclusions", {"target": target})
                if status == 409:
                    raise MaintenanceError(409, f"The {level} {target} already has its own {policies.TYPES[key]} "
                                                "policy, attached outside Maintenance. Detach it in Polaris first.",
                                           "exclusion_conflict", {"target": target})
                have = have | {key}
                self.store.set_exclusion(database_id, level, target, [t for t in policies.TYPES if t in have], by)

    async def delete_policy(self, caller, database_id):
        """Detach the database's policies, remove the ones Maintenance created, its exclusions and the schedule."""
        database, _, _ = await self.database_for(caller, database_id, "admin")
        catalog = await self.catalog_for(database, "write")
        attached = await catalog.catalog_policies(fresh=True)
        exclusions = self.store.exclusions(database_id)
        if not attached and not exclusions:
            raise MaintenanceError(404, "This database has no policy.", "no_policy")
        try:
            for entry in attached.values():
                if entry.get("namespace") == [policies.HOME]:
                    await catalog.drop_policy([policies.HOME], entry["name"])
                else:
                    await catalog.detach(entry["namespace"], entry["name"], {"type": "catalog"})
            for name in policies.EXCLUDED_NAMES.values():
                await catalog.drop_policy([policies.HOME], name)
            await catalog.drop_home()
        finally:
            self.polaris.changed(database_id)
        self.store.delete_exclusions(database_id)
        self.store.delete_schedule(database_id)
        return {"deleted": True, "database": database_id,
                "note": "Scheduled maintenance stops. Table health and past runs stay."}

    async def set_schedule(self, caller, database_id, cron, enabled=True):
        """Maintain a database on a cron schedule. The schedule applies the policy, so it needs one."""
        database, me, _ = await self.database_for(caller, database_id, "admin")
        cron = (cron or "").strip()
        self.check_cron(cron)
        catalog = await self.catalog_for(database)
        if not await catalog.catalog_policies(fresh=True):
            raise MaintenanceError(409, "Set a policy for this database first: a schedule applies it.",
                                   "policy_required")
        next_run = croniter(cron, time.time()).get_next(float) if enabled else None
        saved = self.store.set_schedule(database_id, database["team"], database["environment"], cron, enabled,
                                        next_run, me["user"]["name"])
        return self.public_schedule(saved, self.store.last_scheduled_runs([database_id]).get(database_id))

    async def delete_schedule(self, caller, database_id):
        await self.database_for(caller, database_id, "admin")
        if not self.store.delete_schedule(database_id):
            raise MaintenanceError(404, "This database has no schedule.", "no_schedule")
        return {"deleted": True, "database": database_id}

    @staticmethod
    def check_cron(cron):
        """Five fields, valid, and at most once an hour: stricter than the platform's other schedules (five
        minutes), because a run rewrites files and expires snapshots (DECISIONS.md)."""
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
        database, me, role = await self.database_for(caller, database_id, "writer")
        if mode == "maintain":
            catalog = await self.catalog_for(database)
            if not await catalog.catalog_policies(fresh=True):
                raise MaintenanceError(409, "Set a policy for this database first. A dry run shows what the policies "
                                            "that apply would do.", "policy_required")
        triggered_by, agent = self.triggered(caller, me)
        run = await self.start_run(database, mode, tables or [], triggered_by=triggered_by, agent=agent)
        if wait:
            run = await self.runs.wait(run["id"], wait)
        return self.public_run_detail(run, database, role)

    async def start_run(self, database, mode, tables, *, triggered_by, agent="", schedule=""):
        if self.store.active_run(database["id"]):
            raise MaintenanceError(409, "This database already has a run in progress.", "run_in_progress")
        if len(tables) > 200 or any(not isinstance(t, str) or len(t) > 300 or "." not in t for t in tables):
            raise MaintenanceError(422, "Name at most 200 tables, as namespace.table.", "invalid_request")
        kind, dry_run = MODES[mode]
        principal = await self.bridge.principal_for(database["team"], database["environment"])
        return self.runs.start(database=database["id"], team=database["team"], environment=database["environment"],
                               kind=kind, dry_run=dry_run, tables=sorted(set(tables)), principal=principal,
                               triggered_by=triggered_by, agent=agent, schedule=schedule)

    async def list_runs(self, caller, database_id, limit=20):
        await self.database_for(caller, database_id)
        return {"runs": [self.public_run(r) for r in self.store.runs(database_id, limit)]}

    async def get_run(self, caller, run_id, wait=0):
        """The run; with `wait`, as it is when it finishes or after that many seconds, whichever comes first."""
        run, database, _, role = await self.run_for(caller, run_id)
        if wait and run["status"] in ACTIVE:
            run = await self.runs.wait(run_id, wait) or run
        return self.public_run_detail(run, database, role)

    async def get_run_logs(self, caller, run_id, limit=500):
        run, _, _, _ = await self.run_for(caller, run_id)
        return {"run": run_id, "status": run["status"], "lines": await self.runs.logs(run_id, limit)}

    async def cancel_run(self, caller, run_id):
        run, _, _, _ = await self.run_for(caller, run_id, "writer")
        if run["status"] not in ACTIVE:
            raise MaintenanceError(409, "The run has already finished.", "run_finished")
        await self.runs.cancel(run_id)
        return {"cancelling": True, "run": run_id}
