"""Every capability of Conversational BI, shared by the REST API, the MCP server and the chat agent.

Who may ask what is decided per call from the Bridge's `/me`, never from the client:

- A model in a database of one of your teams: you are a member of that team, in any role.
- A model received through a team data share: one of your teams received the share, the share
  includes the model and every table the model reads, and the platform supports `shared-data`.

Data is always read with a read token of the team's automation principal for the database's
environment, which a team administrator enables once. For a shared model that is the principal of
the recipient team. The principal can read what every member of the team can read, so the checks
above are what keeps one person to their own teams.
"""

import asyncio
import datetime
import json
import time

from . import charts
from .config import ENVIRONMENTS
from .errors import CbiError
from .semantic import compiler, ossie
from .semantic.compiler import ModelRef, QuerySpec

ADMINS = ("admin", "bucket-admin")


class Services:
    def __init__(self, settings, bridge, catalog, executor, results):
        self.settings = settings
        self.bridge = bridge
        self.catalog = catalog
        self.executor = executor
        self.results = results

    # Context and authorization

    async def me(self, caller, *, fresh=False):
        return await self.bridge.me(caller.token, fresh=fresh)

    @staticmethod
    def role(me, team):
        return next((m["role"] for m in me["memberships"] if m["team"] == team), None)

    @staticmethod
    def team_name(me, team):
        return next((m["teamName"] for m in me["memberships"] if m["team"] == team), team)

    async def shared_data(self):
        return "shared-data" in await self.bridge.capabilities()

    async def principals(self):
        return {(p["team"], p["environment"]): p["id"] for p in await self.bridge.automation()
                if p["status"] == "active"}

    async def whoami(self, caller):
        me = await self.me(caller)
        discovery = await self.bridge.discovery()
        return {**me, "extension": self.settings.extension_id, "origin": self.settings.origin,
                "environments": list(ENVIRONMENTS), "userPortalUrl": discovery.get("userPortalUrl", ""),
                "sharedData": "shared-data" in discovery.get("capabilities", [])}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise CbiError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return [{"environment": env, "enabled": env in enabled,
                 "databases": [d["name"] for d in me["databases"] if d["team"] == team and d["environment"] == env],
                 "sharedDatabases": [d["name"] for d in me.get("sharedDatabases", [])
                                     if team in (d.get("recipientTeams") or [d.get("sharedWithTeam")])
                                     and d["environment"] == env],
                 **({"principal": enabled[env]["id"], "enabledBy": enabled[env]["createdBy"]}
                    if env in enabled else {})}
                for env in ENVIRONMENTS]

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise CbiError(422, "Choose development, acceptance or production.", "invalid_request")
        return await self.bridge.enable(caller.token, team, environment)

    async def disable_environment(self, caller, team, environment):
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise CbiError(404, "Conversational BI is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        self.catalog.forget(enabled[environment]["id"])
        return {"disabled": True, "environment": environment}

    async def resolve(self, caller, ref, me=None):
        """Which principal reads this model for this person, or why they cannot."""
        me = me or await self.me(caller)
        own = next((d for d in me["databases"] if d["id"] == ref.database), None)
        if own:
            principal = await self.bridge.principal_for(own["team"], own["environment"])
            return {"principal": principal["id"], "database": own, "shared": False, "team": own["team"],
                    "readingTeam": own["team"]}
        shared = next((d for d in me.get("sharedDatabases", []) if d["id"] == ref.database), None)
        if shared and await self.shared_data():
            if not any(o["kind"] == "semantic-model" and o["namespace"] == ref.namespace and o["name"] == ref.name
                       for o in shared.get("sharedObjects", [])):
                raise CbiError(404, "This semantic model is not shared with your teams.", "unknown_model")
            principals = await self.principals()
            for team in shared.get("recipientTeams") or [shared["sharedWithTeam"]]:
                if principal := principals.get((team, shared["environment"])):
                    return {"principal": principal, "database": shared, "shared": True, "team": shared["team"],
                            "readingTeam": team}
            team = (shared.get("recipientTeams") or [shared["sharedWithTeam"]])[0]
            raise CbiError(409, f"Conversational BI is not enabled for {shared['environment']} in "
                                f"{self.team_name(me, team)}, the team that received this share. A team "
                                "administrator enables it with enable_environment.", "environment_not_enabled",
                           {"team": team, "environment": shared["environment"]})
        raise CbiError(404, "No such semantic model in your teams or your teams' data shares.", "unknown_model")

    async def load(self, caller, ref, me=None):
        me = me or await self.me(caller)
        access = await self.resolve(caller, ref, me)
        principal = access["principal"]
        model = ossie.parse(await self.catalog.load_model(principal, ref.database, ref.namespace, ref.name),
                            ref.namespace)
        if access["shared"]:
            tables = {(tuple(o["namespace"]), o["name"]) for o in access["database"].get("sharedObjects", [])
                      if o["kind"] == "table"}
            missing = sorted(".".join([*d.namespace, d.table]) for d in model.datasets
                             if (d.namespace, d.table) not in tables)
            if missing:
                raise CbiError(403, "The share includes this model but not every table it reads. Ask the owning "
                                    "team to add them to the share.", "tables_not_shared", {"tables": missing})
        loaded = await asyncio.gather(*(self.catalog.table_schema(principal, ref.database, list(d.namespace), d.table)
                                        for d in model.datasets))
        schemas = {d.name: found or {} for d, found in zip(model.datasets, loaded, strict=True)}
        unreadable = sorted(".".join([*d.namespace, d.table]) for d, found in zip(model.datasets, loaded, strict=True)
                            if found is None)
        return access, model, schemas, unreadable

    # Models

    def entry(self, me, database, namespace, name, *, shared, reading_team):
        ref = ModelRef(database=database["id"], namespace=namespace, name=name)
        return {"model": ref.model_dump(), "key": ref.key, "name": name, "namespace": namespace,
                "database": database["id"], "databaseName": database["name"], "environment": database["environment"],
                "team": database["team"], "teamName": database.get("ownerTeamName") or self.team_name(
                    me, database["team"]), "shared": shared, "readingTeam": reading_team,
                "readingTeamName": self.team_name(me, reading_team)}

    async def list_models(self, caller):
        me = await self.me(caller)
        principals = await self.principals()
        found, not_enabled = [], {}

        async def own(database):
            principal = principals.get((database["team"], database["environment"]))
            if not principal:
                not_enabled[(database["team"], database["environment"])] = True
                return []
            items = []
            for namespace in await self.catalog.namespaces(principal, database["id"]):
                for name in await self.catalog.model_names(principal, database["id"], namespace):
                    items.append(self.entry(me, database, namespace, name, shared=False, reading_team=database["team"]))
            return items

        for items in await asyncio.gather(*(own(d) for d in me["databases"])):
            found += items
        if await self.shared_data():
            for database in me.get("sharedDatabases", []):
                recipients = database.get("recipientTeams") or [database["sharedWithTeam"]]
                team = next((t for t in recipients if (t, database["environment"]) in principals), None)
                models = [o for o in database.get("sharedObjects", []) if o["kind"] == "semantic-model"]
                if models and team is None:
                    not_enabled[(recipients[0], database["environment"])] = True
                for o in models if team else []:
                    found.append(self.entry(me, database, o["namespace"], o["name"], shared=True, reading_team=team))
        return {
            "models": sorted(found, key=lambda m: (m["environment"], m["shared"], m["databaseName"], m["key"])),
            "notEnabled": [{"team": t, "teamName": self.team_name(me, t), "environment": e,
                            "canEnable": self.role(me, t) in ADMINS} for t, e in sorted(not_enabled)],
        }

    async def describe_model(self, caller, ref):
        access, model, schemas, unreadable = await self.load(caller, ref)
        homes = {}
        for metric in model.metrics:
            try:
                tree = compiler.expressions.parse(metric.expression)
                homes[metric.name] = compiler.metric_home(model, metric, tree)
            except CbiError as exc:
                homes[metric.name] = None
                homes.setdefault("_errors", []).append({"metric": metric.name, "error": str(exc)})
        errors = homes.pop("_errors", [])
        return {
            "model": ref.model_dump(), "name": model.name or ref.name, "description": model.description,
            "instructions": model.instructions, "synonyms": model.synonyms,
            "database": access["database"]["name"], "environment": access["database"]["environment"],
            "shared": access["shared"], "unreadableTables": unreadable,
            "datasets": [{
                "name": d.name, "description": d.description, "table": ".".join([*d.namespace, d.table]),
                "primaryKey": list(d.primary_key),
                "fields": [{"name": f.name, "description": f.description,
                            "type": compiler.field_type(d, f, schemas), "dimension": f in compiler.dimension_fields(d)}
                           for f in d.fields],
            } for d in model.datasets],
            "relationships": [{"name": r.name, "from": r.source, "to": r.target,
                               "fromColumns": list(r.source_columns), "toColumns": list(r.target_columns)}
                              for r in model.relationships],
            "metrics": [{"name": m.name, "description": m.description, "dataset": homes.get(m.name),
                         "unit": compiler.unit(m.description)} for m in model.metrics],
            "dimensions": {home: compiler.reachable(model, home) for home in sorted({h for h in homes.values() if h})},
            "problems": errors,
        }

    async def model_context(self, caller, ref, me=None):
        """What the chat agent is told about the selected model: names and the owner's guidance, as data."""
        _, model, _, _ = await self.load(caller, ref, me)
        return {"label": f"{model.name or ref.name} ({ref.key})", "description": model.description[:1500],
                "guidance": model.instructions[:6000], "metrics": [m.name for m in model.metrics],
                "datasets": [d.name for d in model.datasets]}

    # Queries

    async def run_query(self, caller, ref, spec: QuerySpec, *, me=None):
        access, model, schemas, unreadable = await self.load(caller, ref, me)
        compiled = compiler.compile_query(model, schemas, spec, self.settings.max_rows)
        missing = sorted({".".join([*b["namespace"], b["table"]]) for b in compiled.bindings} & set(unreadable))
        if missing:
            raise CbiError(403, "A table this query needs is missing or not readable.", "table_not_readable",
                           {"tables": missing})
        discovery = await self.bridge.discovery()
        principal = access["principal"]
        job = {"uri": await self.catalog.uri(), "warehouse": ref.database, "token": await self.catalog.token(principal),
               "s3Endpoint": discovery["storage"]["internalEndpoint"], "bindings": compiled.bindings,
               "sql": compiled.sql, "params": compiled.params, "limit": compiled.limit}
        started = time.monotonic()
        try:
            out = await self.executor.run(job)
        except CbiError as exc:
            if exc.code == "table_not_readable":
                self.catalog.forget(principal)
            raise
        types = {c["name"]: c["type"] for c in out["columns"]}
        snapshots = out.get("snapshots", {})
        # The metrics' own dataset dates the answer; lookup tables such as airports change rarely.
        committed = (snapshots.get(compiled.metrics[0]["dataset"]) or {}).get("committedAt")
        result = {
            "model": ref.model_dump(), "modelName": model.name or ref.name, "shared": access["shared"],
            "sql": compiled.pretty, "params": compiled.params, "joinPaths": compiled.join_paths,
            "columns": [{**c, "sqlType": types.get(c["name"])} for c in compiled.columns],
            "metrics": compiled.metrics, "rows": out["rows"], "rowCount": len(out["rows"]),
            "truncated": out["truncated"], "snapshots": snapshots,
            "dataAsOf": datetime.datetime.fromtimestamp(committed / 1000, datetime.UTC).isoformat(
                timespec="seconds").replace("+00:00", "Z") if committed else None,
            "recommended": charts.recommend(compiled.columns, out["rows"]),
            "elapsedMs": round((time.monotonic() - started) * 1000),
            "query": spec.model_dump(exclude_defaults=True),
        }
        id = self.results.put(caller.subject, result)
        return self.results.get(caller.subject, id)

    async def get_result(self, caller, id):
        result = self.results.get(caller.subject, id)
        if result is None:
            raise CbiError(404, "This result expired or belongs to someone else. Ask the question again.",
                           "unknown_result")
        # Access can end after the question: a share revoked, a membership removed.
        await self.resolve(caller, ModelRef.model_validate(result["model"]))
        return result

    def for_llm(self, result, rows=None):
        """A compact view for the LLM: the numbers it may cite and the definitions it must state."""
        limit = self.settings.llm_rows if rows is None else rows
        sample, size = [], 0
        for row in result["rows"][:limit]:
            size += len(json.dumps(row))
            if size > self.settings.llm_bytes:
                break
            sample.append(row)
        return {
            "resultId": result["id"], "model": result["modelName"],
            "columns": [{k: c.get(k) for k in ("name", "kind", "unit", "grain") if c.get(k) is not None}
                        for c in result["columns"]],
            "rowCount": result["rowCount"], "truncated": result["truncated"], "dataAsOf": result["dataAsOf"],
            "recommended": {"tool": result["recommended"]["tool"],
                            "arguments": {"resultId": result["id"], **result["recommended"]["arguments"]}},
            "rowsShown": len(sample),
            "rows": sample, "joinPaths": result["joinPaths"],
            "metrics": [{"name": m["name"], "definition": m["description"], "sql": m["expression"]}
                        for m in result["metrics"]],
            "sql": result["sql"],
        }
