"""Every capability of the Ingestion extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the pipeline's team | Allowed |
| --- | --- |
| reader | View pipelines, runs, logs and schedules; validate a definition |
| writer | Also run pipelines and stop runs |
| admin, bucket-admin | Also create, change and delete pipelines, set their secrets and schedules, enable environments |

Runs always execute as the team's automation principal for the pipeline's environment, never as
the person or agent that starts them.
"""

import asyncio
import time

from croniter import croniter

from . import templates, validate
from .config import ENVIRONMENTS
from .errors import IngestionError

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
DLT_TABLES = "dlt adds the columns _dlt_load_id and _dlt_id to each table, and child tables named <table>__<field> " \
             "for nested lists. Its own bookkeeping stays with the run, never in your namespace."


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
    def require(role, minimum, what="the pipeline's team"):
        if minimum == "writer" and role not in WRITERS:
            raise IngestionError(403, f"This needs the writer or administrator role in {what}.", "forbidden_role")
        if minimum == "admin" and role not in ADMINS:
            raise IngestionError(403, f"This needs the administrator role in {what}.", "forbidden_role")

    async def pipeline_for(self, caller, pipeline_id, minimum="reader"):
        pipeline = self.store.pipeline(pipeline_id)
        me = await self.me(caller)
        role = self.role(me, pipeline["team"]) if pipeline else None
        if not pipeline or not role:
            raise IngestionError(404, "No such pipeline in your teams.", "unknown_pipeline")
        self.require(role, minimum)
        return pipeline, me, role

    async def run_for(self, caller, run_id, minimum="reader"):
        run = self.store.run(run_id)
        if not run:
            raise IngestionError(404, "No such run in your teams.", "unknown_run")
        try:
            pipeline, me, role = await self.pipeline_for(caller, run["pipeline"], minimum)
        except IngestionError as exc:
            if exc.code == "unknown_pipeline":
                raise IngestionError(404, "No such run in your teams.", "unknown_run") from None
            raise
        return run, pipeline, me, role

    @staticmethod
    def triggered(caller, me):
        return me["user"]["name"], caller.client if caller.agent else ""

    def url(self, pipeline_id):
        return f"{self.settings.origin}/#/pipelines/{pipeline_id}"

    @staticmethod
    def database_name(me, database_id):
        return next((d["name"] for d in me["databases"] if d["id"] == database_id), None)

    def public(self, pipeline, me=None, role=None, last_run=None):
        needed = set(validate.refs(pipeline["source"]))
        stored = set(pipeline["secrets"])
        schedule = self.store.schedule(pipeline["id"])
        return {
            "id": pipeline["id"], "name": pipeline["name"], "description": pipeline["description"],
            "team": pipeline["team"], "environment": pipeline["environment"], "database": pipeline["database"],
            "databaseName": self.database_name(me, pipeline["database"]) if me else None,
            "namespace": pipeline["namespace"], "sourceType": pipeline["source_type"], "source": pipeline["source"],
            "resources": pipeline["resources"], "secretsSet": sorted(stored & needed),
            "secretsMissing": sorted(needed - stored), "revision": pipeline["revision"],
            "createdAt": pipeline["created_at"], "createdBy": pipeline["created_by"],
            "updatedAt": pipeline["updated_at"], "updatedBy": pipeline["updated_by"],
            "role": role, "url": self.url(pipeline["id"]), "schedule": self.public_schedule(schedule),
            "lastRun": self.public_run(last_run) if last_run else None,
        }

    @staticmethod
    def public_schedule(schedule):
        if not schedule:
            return None
        return {"cron": schedule["cron"], "enabled": bool(schedule["enabled"]), "nextRunAt": schedule["next_run_at"],
                "createdBy": schedule["created_by"], "lastRun": schedule["last_run"] or None}

    @staticmethod
    def public_run(run):
        return {
            "id": run["id"], "pipeline": run["pipeline"], "status": run["status"], "environment": run["environment"],
            "revision": run["revision"], "fullRefresh": bool(run["full_refresh"]), "triggeredBy": run["triggered_by"],
            "agent": run["agent"] or None, "schedule": bool(run["schedule"]), "createdAt": run["created_at"],
            "startedAt": run["started_at"], "finishedAt": run["finished_at"], "exitCode": run["exit_code"],
            "summary": {k: v for k, v in run["summary"].items() if k != "principal"}, "error": run["error"] or None,
        }

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
                "environments": list(ENVIRONMENTS), "sourceTypes": list(validate.SOURCE_TYPES),
                "allowPrivateSources": self.settings.allow_private,
                "enabledEnvironments": [{"team": team, "environments": envs} for team, envs in enabled.items()],
                "userPortalUrl": discovery.get("userPortalUrl", "")}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise IngestionError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return [{"environment": env, "enabled": env in enabled,
                 "databases": [{"id": d["id"], "name": d["name"]} for d in me["databases"]
                               if d["team"] == team and d["environment"] == env],
                 **({"principal": enabled[env]["id"], "enabledBy": enabled[env].get("createdBy")}
                    if env in enabled else {})}
                for env in ENVIRONMENTS]

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise IngestionError(422, "Choose development, acceptance or production.", "invalid_request")
        # The Bridge checks that the caller administers the team.
        return await self.bridge.enable(caller.token, team, environment)

    async def disable_environment(self, caller, team, environment):
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise IngestionError(404, "Ingestion is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return {"disabled": True, "environment": environment}

    async def list_templates(self, caller):
        await self.me(caller)
        return [{**t, "note": DLT_TABLES} for t in templates.TEMPLATES]

    # Pipelines

    async def list_pipelines(self, caller, team=None, environment=None):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        pipelines = [p for p in self.store.pipelines(list(roles))
                     if team in (None, p["team"]) and environment in (None, p["environment"])]
        last = self.store.last_runs([p["id"] for p in pipelines])
        return [self.public(p, me, roles[p["team"]], last.get(p["id"])) for p in pipelines]

    async def get_pipeline(self, caller, pipeline_id):
        pipeline, me, role = await self.pipeline_for(caller, pipeline_id)
        runs = self.store.runs(pipeline_id, 10)
        return {**self.public(pipeline, me, role, runs[0] if runs else None),
                "recentRuns": [self.public_run(r) for r in runs], "note": DLT_TABLES}

    def check(self, data, existing=None):
        """A pipeline definition with a change applied, checked as a whole."""
        merged = {"sourceType": existing["source_type"], "source": existing["source"],
                  "resources": existing["resources"]} if existing else {}
        merged.update({k: v for k, v in data.items() if k in ("sourceType", "source", "resources") and v is not None})
        if not merged.get("sourceType") or merged.get("source") is None:
            raise IngestionError(422, "A pipeline needs sourceType and source.", "invalid_pipeline")
        return validate.definition(merged["sourceType"], merged["source"], merged.get("resources"),
                                   allow_private=self.settings.allow_private)

    async def validate_pipeline(self, caller, data):
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
            except IngestionError as exc:
                problems.append(str(exc))
        return {"valid": not problems, "problems": problems, "source": checked["source"],
                "resources": checked["resources"], "secretsNeeded": checked["secrets"],
                "resourceNames": checked["resourceNames"], "note": DLT_TABLES}

    @staticmethod
    def database(me, team, environment, database, raise_=True):
        """A team database in one environment, by id or display name."""
        for d in me["databases"]:
            if d["team"] == team and d["environment"] == environment and database in (d["id"], d["name"]):
                return d
        if raise_:
            raise IngestionError(422, "Choose one of the team's databases in this environment.", "unknown_database")
        return None

    async def create_pipeline(self, caller, data):
        me = await self.me(caller)
        team, environment = data["team"], data["environment"]
        self.require(self.role(me, team), "admin", "this team")
        if environment not in ENVIRONMENTS:
            raise IngestionError(422, "Choose development, acceptance or production.", "invalid_request")
        database = self.database(me, team, environment, data["database"])
        checked = await asyncio.to_thread(self.check, data)
        name = validate.pipeline_name(data["name"])
        pipeline = self.store.create_pipeline(
            team=team, environment=environment, name=name, description=(data.get("description") or "")[:500],
            database=database["id"], namespace=validate.namespace(data["namespace"]),
            source_type=data["sourceType"], source=checked["source"], resources=checked["resources"], secrets={},
            created_by=me["user"]["name"])
        if pipeline is None:
            raise IngestionError(409, "A pipeline with this name already exists in this team and environment.",
                                 "name_taken")
        result = self.public(pipeline, me, self.role(me, team))
        if checked["secrets"]:
            result["hint"] = (f"Set {', '.join(checked['secrets'])} with set_pipeline_secrets before the first run. "
                              "Secrets are write-only.")
        return result

    async def update_pipeline(self, caller, pipeline_id, data, expected_revision=None):
        pipeline, me, role = await self.pipeline_for(caller, pipeline_id, "admin")
        if self.store.active_run(pipeline_id):
            raise IngestionError(409, "The pipeline is running. Change it after the run.", "run_in_progress")
        checked = await asyncio.to_thread(self.check, data, pipeline)
        fields = {"source": checked["source"], "resources": checked["resources"],
                  "updated_by": me["user"]["name"]}
        if data.get("sourceType"):
            fields["source_type"] = data["sourceType"]
        if data.get("name") is not None:
            fields["name"] = validate.pipeline_name(data["name"])
        if data.get("description") is not None:
            fields["description"] = data["description"][:500]
        if data.get("namespace") is not None:
            fields["namespace"] = validate.namespace(data["namespace"])
        if data.get("database") is not None:
            fields["database"] = self.database(me, pipeline["team"], pipeline["environment"], data["database"])["id"]
        hints = []
        # Secrets belong to the place a pipeline connects to: a new host gets none of the old ones.
        before = validate.endpoint_identity(pipeline["source_type"], pipeline["source"])
        if pipeline["secrets"] and checked["endpoint"] != before:
            fields["secrets"] = {}
            hints.append("The pipeline now connects elsewhere, so its secrets were removed. Set them again.")
        else:
            fields["secrets"] = {k: v for k, v in pipeline["secrets"].items() if k in checked["secrets"]}
        # dlt's state describes the old tables; the next run starts from scratch.
        moved = (fields.get("namespace", pipeline["namespace"]) != pipeline["namespace"] or
                 fields.get("database", pipeline["database"]) != pipeline["database"] or
                 fields.get("source_type", pipeline["source_type"]) != pipeline["source_type"])
        if moved:
            hints.append("The pipeline loads into a new place, so its next run starts from scratch.")
        updated = self.store.update_pipeline(pipeline_id, expected_revision, forget_state=moved, **fields)
        if updated is None:
            raise IngestionError(409, "A pipeline with this name already exists in this team and environment.",
                                 "name_taken")
        if updated is False:
            if moved and self.store.active_run(pipeline_id):
                raise IngestionError(409, "The pipeline is running. Change it after the run.", "run_in_progress")
            raise IngestionError(409, "The pipeline changed since you read it. Read it again.", "revision_conflict")
        result = self.public(updated, me, role)
        return {**result, "hint": " ".join(hints)} if hints else result

    async def delete_pipeline(self, caller, pipeline_id, confirm_name):
        pipeline, _, _ = await self.pipeline_for(caller, pipeline_id, "admin")
        if confirm_name != pipeline["name"]:
            raise IngestionError(422, "confirm_name must be the pipeline's name.", "confirmation_mismatch")
        if self.store.active_run(pipeline_id):
            raise IngestionError(409, "The pipeline is running. Stop the run first.", "run_in_progress")
        for run in self.store.runs(pipeline_id, 100_000):
            await self.runs.discard(run["id"])
        self.store.delete_pipeline(pipeline_id)
        return {"deleted": True, "id": pipeline_id,
                "note": "Tables the pipeline loaded stay in the database, with their data and properties."}

    async def set_pipeline_secrets(self, caller, pipeline_id, values):
        """Set, replace or (with null) remove secrets. Their values are never returned."""
        pipeline, me, role = await self.pipeline_for(caller, pipeline_id, "admin")
        needed = set(validate.refs(pipeline["source"]))
        validate.secret_values(values, needed)
        stored = dict(pipeline["secrets"])
        for name, value in values.items():
            if value is None:
                stored.pop(name, None)
            else:
                stored[name] = self.secrets.seal(name, value)
        self.store.update_pipeline(pipeline_id, None, secrets=stored, updated_by=me["user"]["name"])
        return self.public(self.store.pipeline(pipeline_id), me, role)

    # Runs

    async def run_pipeline(self, caller, pipeline_id, full_refresh=False, wait=0):
        pipeline, me, _ = await self.pipeline_for(caller, pipeline_id, "writer")
        triggered_by, agent = self.triggered(caller, me)
        run = await self.start_run(pipeline, triggered_by=triggered_by, agent=agent, full_refresh=full_refresh)
        if wait:
            run = await self.runs.wait(run["id"], wait)
        return self.public_run(run)

    async def start_run(self, pipeline, *, triggered_by, agent="", schedule="", full_refresh=False):
        missing = sorted(set(validate.refs(pipeline["source"])) - set(pipeline["secrets"]))
        if missing:
            raise IngestionError(409, f"Set the secrets {', '.join(missing)} first.", "secrets_missing")
        if self.store.active_run(pipeline["id"]):
            raise IngestionError(409, "This pipeline is already running.", "run_in_progress")
        # The definition is checked again: the platform's address rules may have changed since it was saved.
        await asyncio.to_thread(validate.definition, pipeline["source_type"], pipeline["source"],
                                pipeline["resources"], allow_private=self.settings.allow_private)
        principal = await self.bridge.principal_for(pipeline["team"], pipeline["environment"])
        return self.runs.start(pipeline=pipeline, principal=principal, triggered_by=triggered_by, agent=agent,
                               schedule=schedule, full_refresh=full_refresh)

    async def list_runs(self, caller, pipeline_id, limit=20):
        await self.pipeline_for(caller, pipeline_id)
        return [self.public_run(r) for r in self.store.runs(pipeline_id, limit)]

    async def get_run(self, caller, run_id):
        run, _, _, _ = await self.run_for(caller, run_id)
        return self.public_run(run)

    async def get_run_logs(self, caller, run_id, tail=500):
        run, _, _, _ = await self.run_for(caller, run_id)
        return {"run": run_id, "status": run["status"], "lines": await self.runs.logs(run_id, tail)}

    async def cancel_run(self, caller, run_id):
        run, _, _, _ = await self.run_for(caller, run_id, "writer")
        if run["status"] not in ("queued", "running"):
            raise IngestionError(409, "The run has already finished.", "run_finished")
        await self.runs.cancel(run_id)
        return {"cancelling": True, "run": run_id}

    # Schedules

    async def set_schedule(self, caller, pipeline_id, cron, enabled=True):
        _, me, _ = await self.pipeline_for(caller, pipeline_id, "admin")
        if not croniter.is_valid(cron) or len(cron.split()) != 5:
            raise IngestionError(422, "Give a five-field cron expression in UTC, such as '0 5 * * *'.",
                                 "invalid_cron")
        now = time.time()
        if self.too_often(cron, now):
            raise IngestionError(422, "Schedules run at most every 5 minutes.", "invalid_cron")
        schedule = self.store.set_schedule(pipeline_id, cron=cron, enabled=int(enabled),
                                           next_run_at=croniter(cron, now).get_next(float),
                                           created_by=me["user"]["name"])
        return self.public_schedule(schedule)

    @staticmethod
    def too_often(cron, now):
        times = croniter(cron, now)
        first, second = times.get_next(float), times.get_next(float)
        return second - first < 300

    async def delete_schedule(self, caller, pipeline_id):
        await self.pipeline_for(caller, pipeline_id, "admin")
        if not self.store.delete_schedule(pipeline_id):
            raise IngestionError(404, "This pipeline has no schedule.", "no_schedule")
        return {"deleted": True, "pipeline": pipeline_id}
