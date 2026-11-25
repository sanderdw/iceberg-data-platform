"""Every capability of the Replication extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the replication's team | Allowed |
| --- | --- |
| reader | View replications, runs, logs, source checks and schedules; validate a definition |
| writer | Also run replications, check their source and stop runs |
| admin, bucket-admin | Also create, change and delete replications, set their secrets and schedules, enable |
| | environments |

Runs always execute as the team's automation principal for the replication's environment, never as
the person or agent that starts them.
"""

import asyncio
import time
from datetime import UTC, datetime

from croniter import croniter

from . import templates, validate
from .config import ENVIRONMENTS
from .errors import ReplicationError

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
COLUMNS = ("OLake adds the columns _olake_id, _olake_timestamp, _op_type, _cdc_lsn and _cdc_timestamp. A row deleted "
           "in the source stays in the table with _op_type = 'd': filter on _op_type <> 'd' for the current rows.")


def timestamp(value):
    """A stored time (seconds since 1970) as every response shows it: RFC 3339 in UTC with milliseconds."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def apps(discovery):
    """The Apps menu's entries (Bridge capability `app-directory`); with an older core, just the user portal."""
    pages = discovery.get("userPortalPages") or (
        [{"id": "portal", "title": "User portal", "url": discovery["userPortalUrl"]}]
        if discovery.get("userPortalUrl") else [])
    return {"workspace": [{k: str(p.get(k, "")) for k in ("id", "title", "url")} for p in pages],
            "extensions": [{k: str(e.get(k) or "") for k in ("id", "title", "purpose", "origin")}
                           for e in discovery.get("extensions") or []]}


class Services:
    def __init__(self, settings, store, bridge, runs, secrets):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.runs = runs
        self.secrets = secrets

    # Context and authorization

    async def me(self, caller):
        return await self.bridge.me(caller.token)

    @staticmethod
    def role(me, team):
        return next((m["role"] for m in me["memberships"] if m["team"] == team), None)

    @staticmethod
    def require(role, minimum, what="the replication's team"):
        if minimum == "writer" and role not in WRITERS:
            raise ReplicationError(403, f"This needs the writer or administrator role in {what}.", "forbidden_role")
        if minimum == "admin" and role not in ADMINS:
            raise ReplicationError(403, f"This needs the administrator role in {what}.", "forbidden_role")

    async def replication_for(self, caller, replication_id, minimum="reader"):
        replication = self.store.replication(replication_id)
        me = await self.me(caller)
        role = self.role(me, replication["team"]) if replication else None
        if not replication or not role:
            raise ReplicationError(404, "No such replication in your teams.", "unknown_replication")
        self.require(role, minimum)
        return replication, me, role

    async def run_for(self, caller, run_id, minimum="reader"):
        run = self.store.run(run_id)
        if not run:
            raise ReplicationError(404, "No such run in your teams.", "unknown_run")
        try:
            replication, me, role = await self.replication_for(caller, run["replication"], minimum)
        except ReplicationError as exc:
            if exc.code == "unknown_replication":
                raise ReplicationError(404, "No such run in your teams.", "unknown_run") from None
            raise
        return run, replication, me, role

    @staticmethod
    def triggered(caller, me):
        return me["user"]["name"], caller.client if caller.agent else ""

    def url(self, replication_id):
        return f"{self.settings.origin}/#/replications/{replication_id}"

    @staticmethod
    def database_name(me, database_id):
        return next((d["name"] for d in me["databases"] if d["id"] == database_id), None)

    def rules(self):
        return {"allowed": self.settings.allowed_networks, "allow_private": self.settings.allow_private}

    def public(self, replication, me=None, role=None, last_run=None):
        needed = set(validate.refs(replication["source"]))
        stored = set(replication["secrets"])
        schedule = self.store.schedule(replication["id"])
        check = self.store.last_check(replication["id"])
        # Tables without a primary key, as the last source check found them.
        found = ((check or {}).get("summary") or {}).get("streams") or []
        keyless = {s["name"] for s in found if not s.get("primaryKey")}
        return {
            "id": replication["id"], "name": replication["name"], "description": replication["description"],
            "team": replication["team"], "environment": replication["environment"],
            "database": replication["database"],
            "databaseName": self.database_name(me, replication["database"]) if me else None,
            "namespace": replication["namespace"], "sourceType": replication["source_type"],
            "source": replication["source"], "tables": replication["tables"], "options": replication["options"],
            "secretsSet": sorted(stored & needed), "secretsMissing": sorted(needed - stored),
            "revision": replication["revision"], "createdAt": timestamp(replication["created_at"]),
            "createdBy": replication["created_by"], "updatedAt": timestamp(replication["updated_at"]),
            "updatedBy": replication["updated_by"], "role": role, "url": self.url(replication["id"]),
            "schedule": self.public_schedule(schedule), "lastRun": self.public_run(last_run) if last_run else None,
            "lastCheck": self.public_check(check) if check else None,
            "setupSql": templates.setup_sql(replication["source_type"], replication["source"],
                                            replication["tables"], keyless),
            "warnings": templates.warnings(replication["tables"], keyless),
        }

    @staticmethod
    def public_schedule(schedule):
        if not schedule:
            return None
        enabled = bool(schedule["enabled"])
        return {"cron": schedule["cron"], "enabled": enabled,
                "nextRunAt": timestamp(schedule["next_run_at"]) if enabled else None,
                "lastRun": schedule["last_run"] or None, "createdBy": schedule["created_by"],
                "createdAt": timestamp(schedule["created_at"])}

    @staticmethod
    def public_run(run, results=False):
        """A run as every response shows it. `results` adds the tables it wrote: for one run, never in a list."""
        summary = run["summary"]
        # The runner's totals. The account the run acted as is not shown, the run's own status is the one status,
        # the tables are the run's results, and a source check's findings are shown by public_check.
        hidden = ("principal", "status", "tables", "checks", "streams")
        shown = {
            "id": run["id"], "replication": run["replication"], "environment": run["environment"],
            "status": run["status"], "triggeredBy": run["triggered_by"], "agent": run["agent"] or None,
            "scheduled": bool(run["schedule"]), "kind": run["kind"], "revision": run["revision"],
            "fullRefresh": bool(run["full_refresh"]), "createdAt": timestamp(run["created_at"]),
            "startedAt": timestamp(run["started_at"]), "finishedAt": timestamp(run["finished_at"]),
            "exitCode": run["exit_code"],
            "summary": {k: (v or None) if k == "error" else v for k, v in summary.items() if k not in hidden},
            "error": run["error"] or None,
        }
        return {**shown, "results": summary.get("tables") or []} if results else shown

    @staticmethod
    def public_check(run):
        summary = run["summary"]
        return {"run": run["id"], "status": run["status"],
                "checkedAt": timestamp(run["finished_at"] or run["created_at"]),
                "connected": summary.get("connected"), "ready": summary.get("ready"),
                "message": summary.get("message") or run["error"] or None,
                "checks": summary.get("checks") or [], "tables": summary.get("streams") or []}

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
        """One team environment: whether Replication is enabled there, by whom, and the databases it can write to."""
        return {"team": team, "environment": environment, "enabled": principal is not None,
                "enabledBy": principal.get("createdBy") if principal else None,
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
        return {**self.public_me(me, discovery, enabled), "sourceTypes": list(validate.SOURCE_TYPES),
                "allowPrivateSources": self.settings.allow_private,
                "allowedSourceNetworks": [str(n) for n in self.settings.allowed_networks]}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise ReplicationError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return {"environments": [self.public_environment(me, team, env, enabled.get(env)) for env in ENVIRONMENTS]}

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise ReplicationError(422, "Choose development, acceptance or production.", "invalid_request")
        me = await self.me(caller)
        # The Bridge checks that the caller administers the team.
        principal = await self.bridge.enable(caller.token, team, environment)
        return self.public_environment(me, team, environment, principal)

    async def disable_environment(self, caller, team, environment):
        me = await self.me(caller)
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise ReplicationError(409, "Replication is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return self.public_environment(me, team, environment, None)

    async def list_templates(self, caller):
        await self.me(caller)
        return {"templates": [
            {**t, "setupSql": templates.setup_sql(t["sourceType"], t["source"], t["tables"]), "note": COLUMNS}
            for t in templates.TEMPLATES]}

    # Replications

    async def list_replications(self, caller, team=None, environment=None):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        replications = [r for r in self.store.replications(list(roles))
                        if team in (None, r["team"]) and environment in (None, r["environment"])]
        last = self.store.last_runs([r["id"] for r in replications])
        return {"replications": [self.public(r, me, roles[r["team"]], last.get(r["id"])) for r in replications]}

    async def get_replication(self, caller, replication_id):
        replication, me, role = await self.replication_for(caller, replication_id)
        runs = self.store.runs(replication_id, 10)
        syncs = [r for r in runs if r["kind"] == "sync"]
        return {**self.public(replication, me, role, syncs[0] if syncs else None),
                "recentRuns": [self.public_run(r) for r in runs], "note": COLUMNS}

    def check(self, data, existing=None):
        """A replication definition with a change applied, checked as a whole."""
        merged = {"sourceType": existing["source_type"], "source": existing["source"],
                  "tables": existing["tables"], "options": existing["options"]} if existing else {}
        merged.update({k: v for k, v in data.items()
                       if k in ("sourceType", "source", "tables", "options") and v is not None})
        if not merged.get("sourceType") or merged.get("source") is None:
            raise ReplicationError(422, "A replication needs sourceType and source.", "invalid_replication")
        return validate.definition(merged["sourceType"], merged["source"], merged.get("tables"),
                                   merged.get("options"), **self.rules())

    async def validate_replication(self, caller, data):
        me = await self.me(caller)
        checked = await asyncio.to_thread(self.check, data)
        problems = []
        if data.get("team"):
            if not self.role(me, data["team"]):
                problems.append("You are not a member of this team.")
            elif data.get("environment") and data.get("database") and not self.database(
                    me, data["team"], data["environment"], data["database"], raise_=False):
                problems.append("The database is not one of the team's databases in this environment.")
        if data.get("namespace") is not None:
            try:
                validate.namespace(data["namespace"])
            except ReplicationError as exc:
                problems.append(str(exc))
        return {"valid": not problems, "problems": problems, "source": checked["source"],
                "tables": checked["tables"], "options": checked["options"], "secretsNeeded": checked["secrets"],
                "setupSql": templates.setup_sql(data["sourceType"], checked["source"], checked["tables"]),
                "warnings": templates.warnings(checked["tables"]), "note": COLUMNS}

    @staticmethod
    def database(me, team, environment, database, raise_=True):
        """A team database in one environment, by id or display name."""
        for d in me["databases"]:
            if d["team"] == team and d["environment"] == environment and database in (d["id"], d["name"]):
                return d
        if raise_:
            raise ReplicationError(422, "Choose one of the team's databases in this environment.",
                                   "invalid_database")
        return None

    async def create_replication(self, caller, data):
        me = await self.me(caller)
        team, environment = data["team"], data["environment"]
        self.require(self.role(me, team), "admin", "this team")
        if environment not in ENVIRONMENTS:
            raise ReplicationError(422, "Choose development, acceptance or production.", "invalid_request")
        database = self.database(me, team, environment, data["database"])
        checked = await asyncio.to_thread(self.check, data)
        name = validate.replication_name(data["name"])
        replication = self.store.create_replication(
            team=team, environment=environment, name=name, description=(data.get("description") or "")[:500],
            database=database["id"], namespace=validate.namespace(data["namespace"]),
            source_type=data["sourceType"], source=checked["source"], tables=checked["tables"],
            options=checked["options"], secrets={}, created_by=me["user"]["name"])
        if replication is None:
            raise ReplicationError(409, "A replication with this name already exists in this team and environment.",
                                   "name_taken")
        result = self.public(replication, me, self.role(me, team))
        hints = []
        if checked["secrets"]:
            hints.append(f"Set {', '.join(checked['secrets'])} with set_replication_secrets, then check the source "
                         "with check_source. Secrets are write-only.")
        if not checked["tables"]:
            hints.append("Choose tables with update_replication after check_source has listed them.")
        return {**result, "hint": " ".join(hints)} if hints else result

    async def update_replication(self, caller, replication_id, data, expected_revision=None):
        replication, me, role = await self.replication_for(caller, replication_id, "admin")
        if self.store.active_run(replication_id):
            raise ReplicationError(409, "The replication is running. Change it after the run.", "run_in_progress")
        checked = await asyncio.to_thread(self.check, data, replication)
        fields = {"source": checked["source"], "tables": checked["tables"], "options": checked["options"],
                  "updated_by": me["user"]["name"]}
        if data.get("sourceType"):
            fields["source_type"] = data["sourceType"]
        if data.get("name") is not None:
            fields["name"] = validate.replication_name(data["name"])
        if data.get("description") is not None:
            fields["description"] = data["description"][:500]
        if data.get("namespace") is not None:
            fields["namespace"] = validate.namespace(data["namespace"])
        if data.get("database") is not None:
            fields["database"] = self.database(me, replication["team"], replication["environment"],
                                               data["database"])["id"]
        hints = []
        # Secrets belong to the place a replication connects to: a new host gets none of the old ones.
        before = validate.endpoint_identity(replication["source_type"], replication["source"])
        if replication["secrets"] and checked["endpoint"] != before:
            fields["secrets"] = {}
            hints.append("The replication now connects elsewhere, so its secrets were removed. Set them again.")
        else:
            fields["secrets"] = {k: v for k, v in replication["secrets"].items() if k in checked["secrets"]}
        # OLake's state describes the old source and tables: the next run starts with a new first copy.
        moved = (fields.get("namespace", replication["namespace"]) != replication["namespace"] or
                 fields.get("database", replication["database"]) != replication["database"] or
                 fields.get("source_type", replication["source_type"]) != replication["source_type"] or
                 checked["endpoint"] != before or
                 checked["source"].get("slot") != replication["source"].get("slot"))
        if moved:
            hints.append("The replication reads from or writes to a new place, so its next run copies everything "
                         "again.")
        updated = self.store.update_replication(replication_id, expected_revision, forget_state=moved, **fields)
        if updated is None:
            raise ReplicationError(409, "A replication with this name already exists in this team and environment.",
                                   "name_taken")
        if updated is False:
            if moved and self.store.active_run(replication_id):
                raise ReplicationError(409, "The replication is running. Change it after the run.",
                                       "run_in_progress")
            raise ReplicationError(409, "The replication changed since you read it. Read it again.",
                                   "revision_conflict")
        result = self.public(updated, me, role)
        return {**result, "hint": " ".join(hints)} if hints else result

    async def delete_replication(self, caller, replication_id, confirm_name):
        replication, _, _ = await self.replication_for(caller, replication_id, "admin")
        if confirm_name != replication["name"]:
            raise ReplicationError(422, "The confirmation must be the replication's exact name.",
                                   "confirmation_mismatch")
        if self.store.active_run(replication_id):
            raise ReplicationError(409, "The replication is running. Stop the run first.", "run_in_progress")
        for run in self.store.runs(replication_id, 100_000):
            await self.runs.discard(run["id"])
        self.store.delete_replication(replication_id)
        result = {"deleted": True, "replication": replication_id,
                  "note": "Tables the replication wrote stay in the database, with their data and properties."}
        slot = replication["source"].get("slot")
        if slot:
            result["dropSlotSql"] = f"SELECT pg_drop_replication_slot('{slot}');"
            result["warning"] = (f"Ask the database owner to drop the replication slot {slot}: until then the source "
                                 "keeps its change log, and its disk fills up.")
        return result

    async def set_replication_secrets(self, caller, replication_id, values):
        """Set, replace or (with null) remove secrets. Their values are never returned."""
        replication, me, role = await self.replication_for(caller, replication_id, "admin")
        needed = set(validate.refs(replication["source"]))
        validate.secret_values(values, needed)
        stored = dict(replication["secrets"])
        for name, value in values.items():
            if value is None:
                stored.pop(name, None)
            else:
                stored[name] = self.secrets.seal(name, value)
        self.store.update_replication(replication_id, None, secrets=stored, updated_by=me["user"]["name"])
        return self.public(self.store.replication(replication_id), me, role)

    # Runs

    async def run_replication(self, caller, replication_id, full_refresh=False, wait=0):
        replication, me, _ = await self.replication_for(caller, replication_id, "writer")
        triggered_by, agent = self.triggered(caller, me)
        run = await self.start_run(replication, triggered_by=triggered_by, agent=agent, full_refresh=full_refresh)
        if wait:
            run = await self.runs.wait(run["id"], wait)
        return self.public_run(run, results=True)

    async def check_source(self, caller, replication_id, wait=120):
        """Connects to the source with the stored secrets: OLake's prerequisites and the tables it can replicate."""
        replication, me, _ = await self.replication_for(caller, replication_id, "writer")
        triggered_by, agent = self.triggered(caller, me)
        run = await self.start_run(replication, triggered_by=triggered_by, agent=agent, kind="check")
        if wait:
            run = await self.runs.wait(run["id"], wait)
        if run["status"] in ("queued", "running"):
            return {"run": run["id"], "status": run["status"],
                    "hint": "The check is still running. Read it later with get_replication (lastCheck)."}
        return self.public_check(run)

    async def start_run(self, replication, *, triggered_by, agent="", schedule="", full_refresh=False, kind="sync"):
        missing = sorted(set(validate.refs(replication["source"])) - set(replication["secrets"]))
        if missing:
            raise ReplicationError(409, f"Set the secrets {', '.join(missing)} first.", "secrets_missing")
        if kind == "sync" and not replication["tables"]:
            raise ReplicationError(409, "Choose at least one table first.", "no_tables")
        if self.store.active_run(replication["id"]):
            raise ReplicationError(409, "This replication is already running.", "run_in_progress")
        # The definition is checked again: the platform's address rules may have changed since it was saved, and
        # the source host is resolved now and pinned in the run.
        checked = await asyncio.to_thread(validate.definition, replication["source_type"], replication["source"],
                                          replication["tables"], replication["options"], **self.rules())
        address = await asyncio.to_thread(validate.pin, checked["source"]["host"], **self.rules())
        principal = await self.bridge.principal_for(replication["team"], replication["environment"])
        return self.runs.start(replication=replication, principal=principal, triggered_by=triggered_by, agent=agent,
                               schedule=schedule, full_refresh=full_refresh, kind=kind,
                               hosts={checked["source"]["host"]: address} if address else {})

    async def list_runs(self, caller, replication_id, limit=20):
        await self.replication_for(caller, replication_id)
        return {"runs": [self.public_run(r) for r in self.store.runs(replication_id, limit)]}

    async def get_run(self, caller, run_id, wait=0):
        """The run with its results. `wait` holds the answer until the run is final, for at most that many seconds."""
        run, _, _, _ = await self.run_for(caller, run_id)
        if wait and run["status"] in ("queued", "running"):
            run = await self.runs.wait(run_id, wait)
        return self.public_run(run, results=True)

    async def get_run_logs(self, caller, run_id, limit=500):
        run, _, _, _ = await self.run_for(caller, run_id)
        return {"run": run_id, "status": run["status"], "lines": await self.runs.logs(run_id, limit)}

    async def cancel_run(self, caller, run_id):
        run, _, _, _ = await self.run_for(caller, run_id, "writer")
        if run["status"] not in ("queued", "running"):
            raise ReplicationError(409, "The run has already finished.", "run_finished")
        await self.runs.cancel(run_id)
        return {"cancelling": True, "run": run_id}

    # Schedules

    async def set_schedule(self, caller, replication_id, cron, enabled=True):
        replication, me, _ = await self.replication_for(caller, replication_id, "admin")
        if not croniter.is_valid(cron) or len(cron.split()) != 5:
            raise ReplicationError(422, "Give a five-field cron expression in UTC, such as '*/15 * * * *'.",
                                   "invalid_cron")
        now = time.time()
        if self.too_often(cron, now):
            raise ReplicationError(422, "Schedules run at most every 5 minutes.", "invalid_cron")
        if not replication["tables"]:
            raise ReplicationError(409, "Choose at least one table first.", "no_tables")
        schedule = self.store.set_schedule(replication_id, cron=cron, enabled=int(enabled),
                                           next_run_at=croniter(cron, now).get_next(float),
                                           created_by=me["user"]["name"])
        return self.public_schedule(schedule)

    @staticmethod
    def too_often(cron, now):
        times = croniter(cron, now)
        first, second = times.get_next(float), times.get_next(float)
        return second - first < 300

    async def delete_schedule(self, caller, replication_id):
        await self.replication_for(caller, replication_id, "admin")
        if not self.store.delete_schedule(replication_id):
            raise ReplicationError(404, "This replication has no schedule.", "no_schedule")
        return {"deleted": True, "replication": replication_id}
