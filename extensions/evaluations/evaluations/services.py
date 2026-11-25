"""Every capability of the Evaluations extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the owning team | Allowed |
| --- | --- |
| reader | View question sets, runs, results and schedules |
| writer | Also create, edit and delete question sets and start runs in development |
| admin, bucket-admin | Also enable environments, work in acceptance and production, schedule, run live evaluation |

Reviewing live conversations (dismiss, promote to a case) needs the writer role; the question set a
case goes to follows its own rules.

Runs ask the agent for the team, never as the person who started them: Conversational BI answers
only where a team administrator enabled both extensions, with the scope every team member has.
"""

import time
from datetime import UTC, datetime

from croniter import croniter

from . import questions
from .config import AGENT, ENVIRONMENTS
from .errors import EvalError
from .store import new_id

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
MAX_COMPARE = 5
# How a stored name says that an AI agent acted for the person.
VIA_AGENT = " (via agent)"
# Live evaluation of a team environment nobody set up yet.
LIVE_DEFAULTS = {"enabled": False, "sample_pct": 20, "cron": "0 * * * *", "target_database": ""}
MODEL_KEYS = ("database", "namespace", "name")


def timestamp(value):
    """A stored time (seconds since 1970) as every response shows it: RFC 3339 in UTC with milliseconds."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def seconds(value):
    """A time as responses show it (RFC 3339), as this extension stores it: seconds since 1970."""
    return datetime.fromisoformat(value).timestamp()


def uplift(before, after, baseline_arm, good):
    """What the semantic model adds: both arms' pass rates, overall and per kind, on questions both answered."""
    control, semantic = (before, after) if baseline_arm == "control" else (after, before)
    names = [n for n in control.keys() & semantic.keys() if not control[n]["error"] and not semantic[n]["error"]]

    def rates(chosen):
        c = sum(good(control[n], semantic[n]) for n in chosen) / len(chosen)
        s = sum(good(semantic[n], control[n]) for n in chosen) / len(chosen)
        return {"questions": len(chosen), "control": round(c, 4), "semantic": round(s, 4),
                "points": round((s - c) * 100, 1)}

    kinds = sorted({semantic[n]["kind"] for n in names})
    return {**rates(names), "byKind": {k: rates([n for n in names if semantic[n]["kind"] == k]) for k in kinds}} \
        if names else None


def apps(discovery):
    """The Apps menu's entries (Bridge capability `app-directory`); with an older core, just the user portal."""
    pages = discovery.get("userPortalPages") or (
        [{"id": "portal", "title": "User portal", "url": discovery["userPortalUrl"]}]
        if discovery.get("userPortalUrl") else [])
    return {"workspace": [{k: str(p.get(k, "")) for k in ("id", "title", "url")} for p in pages],
            "extensions": [{k: str(e.get(k) or "") for k in ("id", "title", "purpose", "origin")}
                           for e in discovery.get("extensions") or []]}


class Services:
    def __init__(self, settings, store, bridge, agent, runner):
        self.settings = settings
        self.store = store
        self.bridge = bridge
        self.agent = agent
        self.runner = runner

    # Context and authorization

    async def me(self, caller):
        return await self.bridge.me(caller.token)

    @staticmethod
    def role(me, team):
        return next((m["role"] for m in me["memberships"] if m["team"] == team), None)

    @staticmethod
    def needs(role, environment, action):
        """Writers work in development; acceptance and production are for team administrators."""
        if role is None:
            raise EvalError(404, "No such question set in your teams.", "unknown_question_set")
        if action == "view":
            return
        allowed = ADMINS if action == "admin" or environment != "development" else WRITERS
        if role not in allowed:
            who = "an administrator" if allowed == ADMINS else "a writer or administrator"
            raise EvalError(403, f"This needs {who} of the question set's team.", "forbidden_role")

    async def question_set_for(self, caller, id, action="view"):
        found = self.store.question_set(id)
        me = await self.me(caller)
        role = self.role(me, found["team"]) if found else None
        self.needs(role, found["environment"] if found else "", action)
        return found, me, role

    async def owned(self, caller, question_set, action, what, code):
        """The question set a run or schedule belongs to; one of another team is as unknown as one that is not there."""
        try:
            return await self.question_set_for(caller, question_set, action)
        except EvalError as exc:
            if exc.code == "unknown_question_set":
                raise EvalError(404, f"No such {what} in your teams.", code) from None
            raise

    async def run_for(self, caller, run_id):
        run = self.store.run(run_id)
        if run is None:
            raise EvalError(404, "No such run in your teams.", "unknown_run")
        found, _, _ = await self.owned(caller, run["question_set"], "view", "run", "unknown_run")
        return run, found

    async def schedule_for(self, caller, schedule_id):
        schedule = self.store.schedule(schedule_id)
        if schedule is None:
            raise EvalError(404, "No such schedule in your teams.", "unknown_schedule")
        await self.owned(caller, schedule["question_set"], "admin", "schedule", "unknown_schedule")
        return schedule

    @staticmethod
    def by(caller, me):
        return me["user"]["name"] + (VIA_AGENT if caller.agent else "")

    # Projections: what a response shows of a stored row or of the platform's answer, field by field.

    @staticmethod
    def public_set(item, role=None):
        return {
            "id": item["id"], "team": item["team"], "environment": item["environment"], "name": item["name"],
            "description": item["description"], "agent": item["agent"],
            "model": {k: item["model"][k] for k in MODEL_KEYS}, "rubric": item["rubric"],
            "targetDatabase": item["target_database"] or None, "version": item["version"],
            "createdAt": timestamp(item["created_at"]), "createdBy": item["created_by"],
            "updatedAt": timestamp(item["updated_at"]), "updatedBy": item["updated_by"], "role": role,
        }

    @staticmethod
    def public_version(item):
        return {"version": item["version"], "cases": item["cases"], "note": item["note"] or None,
                "createdAt": timestamp(item["created_at"]), "createdBy": item["created_by"]}

    def public_run(self, run):
        """A run as every response shows it. A finished run is `success`, as in the platform's other extensions."""
        started_by, scheduled = run["triggered_by"], bool(run["schedule"])
        # The LLMs that answered are `llmModels`: a model is always a semantic model. `published` says where the
        # results went, or why not.
        summary = {("llmModels" if k == "models" else k): v for k, v in run["summary"].items()}
        if summary:
            summary["published"] = run["published"] or None
        return {
            "id": run["id"], "questionSet": run["question_set"], "version": run["version"], "team": run["team"],
            "environment": run["environment"], "llmModel": run["llm_model"] or None,
            "status": "success" if run["status"] == "done" else run["status"],
            "triggeredBy": "schedule" if scheduled else started_by.removesuffix(VIA_AGENT),
            "agent": self.settings.mcp_client_id if started_by.endswith(VIA_AGENT) else None,
            "scheduled": scheduled, "schedule": run["schedule"] or None,
            "createdAt": timestamp(run["created_at"]), "startedAt": timestamp(run["started_at"]),
            "finishedAt": timestamp(run["finished_at"]), "summary": summary, "error": run["error"] or None,
            "arm": run["arm"], "pair": run["pair"] or None,
        }

    @staticmethod
    def public_case(case):
        return {
            "name": case["name"], "question": case["question"], "kind": case["kind"], "tags": case["tags"],
            "passed": bool(case["passed"]), "scores": case["scores"], "answer": case["answer"],
            "queries": case["queries"], "toolCalls": case["tool_calls"], "llmModel": case["model"] or None,
            "inputTokens": case["input_tokens"], "outputTokens": case["output_tokens"],
            "durationMs": case["duration_ms"], "traceId": case["trace_id"] or None, "error": case["error"] or None,
        }

    @staticmethod
    def public_schedule(schedule):
        enabled = bool(schedule["enabled"])
        return {
            "id": schedule["id"], "questionSet": schedule["question_set"], "cron": schedule["cron"],
            "llmModel": schedule["llm_model"] or None, "enabled": enabled,
            "nextRunAt": timestamp(schedule["next_run_at"]) if enabled else None,
            "lastRun": schedule["last_run"] or None, "createdBy": schedule["created_by"],
            "createdAt": timestamp(schedule["created_at"]),
        }

    def public_live(self, team, environment, setting):
        """Live evaluation of a team environment. Where it stopped reading conversations stays inside."""
        setting = setting or LIVE_DEFAULTS
        enabled = bool(setting["enabled"])
        summary = {k: (v or None) if k in ("published", "error") else v
                   for k, v in (setting.get("last_summary") or {}).items()}
        return {
            "team": team, "environment": environment, "enabled": enabled, "samplePct": setting["sample_pct"],
            "cron": setting["cron"], "targetDatabase": setting["target_database"] or None,
            "nextRunAt": timestamp(setting["next_run_at"]) if enabled else None,
            "updatedBy": setting.get("updated_by"), "updatedAt": timestamp(setting.get("updated_at")),
            "lastChecked": timestamp(setting.get("last_checked")), "lastSummary": summary or None,
            "openReviews": len(self.store.reviews(team, environment, "open", limit=500)),
        }

    @staticmethod
    def public_review(review):
        return {
            "id": review["id"], "team": review["team"], "environment": review["environment"],
            "conversation": review["conversation"], "askedAt": timestamp(review["asked_at"]),
            "question": review["question"], "answer": review["answer"],
            "model": {k: review["model"][k] for k in MODEL_KEYS}, "queries": review["queries"],
            "llmModel": review["llm_model"] or None, "traceId": review["trace_id"] or None,
            "scores": review["scores"], "status": review["status"], "createdAt": timestamp(review["created_at"]),
            "reviewedBy": review["reviewed_by"] or None, "reviewedAt": timestamp(review["reviewed_at"]),
            "note": review["note"] or None, "promotedTo": review["promoted_to"] or None,
        }

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
        """One team environment: whether Evaluations is enabled there, by whom, and the team's databases in it."""
        return {"team": team, "environment": environment, "enabled": principal is not None,
                "enabledBy": principal.get("createdBy") if principal else None,
                "databases": [{"id": d["id"], "name": d["name"]} for d in me["databases"]
                              if d["team"] == team and d["environment"] == environment]}

    def detail(self, found, role, version=None):
        """A question set with the cases of one version, its versions, schedules and latest runs."""
        stored = self.store.version(found["id"], version or found["version"])
        if stored is None:
            raise EvalError(404, "No such version.", "unknown_version")
        return {**self.public_set(found, role), "yaml": stored["yaml"], "shownVersion": stored["version"],
                "versions": [self.public_version(v) for v in self.store.versions(found["id"])],
                "schedules": [self.public_schedule(s) for s in self.store.schedules(found["id"])],
                "runs": [self.public_run(r) for r in self.store.runs(found["id"], 10)]}

    # Discovery and environments

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
        return {**self.public_me(me, discovery, enabled), "agent": AGENT, "judge": bool(self.settings.judge_model)}

    async def environments(self, caller, team):
        me, _ = await self.member(caller, team)
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return {"environments": [self.public_environment(me, team, env, enabled.get(env)) for env in ENVIRONMENTS]}

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise EvalError(422, "Choose development, acceptance or production.", "invalid_request")
        me = await self.me(caller)
        # The Bridge checks that the caller administers the team.
        principal = await self.bridge.enable(caller.token, team, environment)
        return self.public_environment(me, team, environment, principal)

    async def disable_environment(self, caller, team, environment):
        # Checked here too: live evaluation ends below, before the Bridge is asked.
        me, _ = await self.member(caller, team, "admin")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise EvalError(409, "Evaluations is not enabled for this environment.", "environment_not_enabled")
        setting = self.store.live(team, environment)
        if setting and setting["enabled"]:
            # Live evaluation ends with it: Conversational BI stops keeping conversations and deletes them. If it
            # can't be reached now, it does so at the team's next chat, because Evaluations is disabled by then.
            try:
                await self.agent.recording(team, environment, False)
            except EvalError:
                pass
            self.store.update_live(team, environment, enabled=0)
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return self.public_environment(me, team, environment, None)

    async def agent_models(self, caller, team, environment):
        """The semantic models the agent can answer from for this team, and the LLMs a run may choose."""
        await self.member(caller, team)
        await self.bridge.principal_for(team, environment)
        found = await self.agent.models(team, environment)
        return {"semanticModels": [{"model": {k: m["model"][k] for k in MODEL_KEYS}, "name": m["name"],
                                    "databaseName": m["databaseName"], "environment": m["environment"],
                                    "shared": bool(m["shared"])} for m in found["semanticModels"]],
                "llmModels": [str(name) for name in found["llmModels"]], "schemaOnly": bool(found["schemaOnly"])}

    # Question sets

    async def list_question_sets(self, caller):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        items = []
        for item in self.store.question_sets(list(roles)):
            last = self.store.runs(item["id"], 1)
            items.append({**self.public_set(item, roles[item["team"]]),
                          "lastRun": self.public_run(last[0]) if last else None})
        return {"questionSets": items}

    async def get_question_set(self, caller, id, version=None):
        found, _, role = await self.question_set_for(caller, id)
        return self.detail(found, role, version)

    def name_taken(self, team, environment, name):
        if any(s["name"] == name and s["environment"] == environment for s in self.store.question_sets([team])):
            raise EvalError(409, "This team already has a question set with this name there.", "name_taken")

    async def create_question_set(self, caller, data):
        cases = questions.parse(data["yaml"])
        if not cases:
            raise EvalError(422, "A question set needs at least one case.", "invalid_request")
        me = await self.me(caller)
        team, environment, name = data["team"], data["environment"], data["name"]
        role = self.role(me, team)
        if role is None:
            raise EvalError(403, "You are not a member of this team.", "not_a_member")
        self.needs(role, environment, "edit")
        self.name_taken(team, environment, name)
        created = self.store.create_set(
            team, environment, name, AGENT, data["model"], questions.dump(cases, name), len(cases),
            self.by(caller, me), description=data.get("description") or "", rubric=data.get("rubric") or "",
            target_database=data.get("targetDatabase") or "")
        return self.detail(created, role)

    async def update_question_set(self, caller, id, data):
        """Changes what is given and keeps the rest; new YAML is a new version."""
        found, me, role = await self.question_set_for(caller, id, "edit")
        cases = questions.parse(data["yaml"]) if data.get("yaml") is not None else None
        if cases is not None and not cases:
            raise EvalError(422, "A question set needs at least one case.", "invalid_request")
        fields = {column: data[key] for key, column in (
            ("name", "name"), ("description", "description"), ("rubric", "rubric"),
            ("targetDatabase", "target_database"), ("model", "model")) if data.get(key) is not None}
        name = fields.get("name", found["name"])
        if name != found["name"]:
            self.name_taken(found["team"], found["environment"], name)
        self.store.update_set(id, self.by(caller, me), yaml=questions.dump(cases, name) if cases else None,
                              cases=len(cases) if cases else None, note=data.get("note") or "", **fields)
        return self.detail(self.store.question_set(id), role)

    async def import_questions(self, caller, id, text):
        """Cases from a semantic model's <name>.questions.sql; same names are replaced, others added."""
        found, me, role = await self.question_set_for(caller, id, "edit")
        imported = questions.from_questions_sql(text)
        current = questions.parse(self.store.version(id, found["version"])["yaml"])
        merged = {c.name: c for c in current} | {c.name: c for c in imported}
        if len(merged) > questions.MAX_CASES:
            raise EvalError(422, f"A question set has at most {questions.MAX_CASES} cases.", "invalid_question_set")
        cases = list(merged.values())
        self.store.update_set(id, self.by(caller, me), yaml=questions.dump(cases, found["name"]), cases=len(cases),
                              note=f"imported {len(imported)} questions")
        return {**self.detail(self.store.question_set(id), role), "imported": len(imported)}

    async def delete_question_set(self, caller, id, confirm_name):
        found, _, _ = await self.question_set_for(caller, id, "edit")
        if confirm_name != found["name"]:
            raise EvalError(422, "The confirmation must be the question set's exact name.", "confirmation_mismatch")
        if self.store.active_run(id):
            raise EvalError(409, "A run of this question set is still going.", "run_in_progress")
        self.store.delete_set(id)
        return {"deleted": True, "questionSet": id,
                "note": "Results already written to the team's database stay there."}

    # Runs

    async def start(self, question_set, triggered_by, llm_model="", schedule="", arm="semantic", pair=""):
        await self.bridge.principal_for(question_set["team"], question_set["environment"])
        run = self.store.create_run(question_set["id"], triggered_by, llm_model=llm_model, schedule=schedule, arm=arm,
                                    pair=pair)
        self.runner.start(run)
        return run

    async def run_evaluation(self, caller, id, llm_model=""):
        found, me, _ = await self.question_set_for(caller, id, "run")
        return self.public_run(await self.start(found, self.by(caller, me), llm_model or ""))

    async def compare_llm_models(self, caller, id, llm_models):
        found, me, _ = await self.question_set_for(caller, id, "run")
        llm_models = list(dict.fromkeys(m for m in llm_models if m))
        if not 2 <= len(llm_models) <= MAX_COMPARE:
            raise EvalError(422, f"Compare 2 to {MAX_COMPARE} LLMs.", "invalid_request")
        runs = [await self.start(found, self.by(caller, me), m) for m in llm_models]
        return {"runs": [self.public_run(r) for r in runs]}

    async def measure_semantic_model(self, caller, id, llm_model=""):
        """The same questions and LLM twice: with the semantic model, and with only the table schemas."""
        found, me, _ = await self.question_set_for(caller, id, "run")
        await self.bridge.principal_for(found["team"], found["environment"])
        if not (await self.agent.models(found["team"], found["environment"]))["schemaOnly"]:
            raise EvalError(409, "The control arm is off: a platform administrator sets BI_EVAL_SCHEMA_ONLY=true in "
                                 "Conversational BI, because there an LLM writes SQL.", "schema_only_disabled")
        pair = new_id("pair")
        by = self.by(caller, me)
        control = await self.start(found, by, llm_model or "", arm="control", pair=pair)
        semantic = await self.start(found, by, llm_model or "", pair=pair)
        return {"pair": pair, "control": self.public_run(control), "semantic": self.public_run(semantic)}

    async def list_runs(self, caller, id, limit=20):
        await self.question_set_for(caller, id)
        return {"runs": [self.public_run(r) for r in self.store.runs(id, limit)]}

    async def get_run(self, caller, run_id):
        run, found = await self.run_for(caller, run_id)
        return {**self.public_run(run), "questionSetName": found["name"],
                "cases": [self.public_case(c) for c in self.store.cases(run_id)]}

    async def compare_runs(self, caller, baseline, candidate):
        """Case by case, matched by name: what got better, what got worse."""
        base, base_set = await self.run_for(caller, baseline)
        cand, cand_set = await self.run_for(caller, candidate)
        if base_set["id"] != cand_set["id"]:
            raise EvalError(422, "Compare runs of one question set.", "invalid_request")
        before = {c["name"]: c for c in self.store.cases(baseline)}
        after = {c["name"]: c for c in self.store.cases(candidate)}
        arms = base["arm"] != cand["arm"]

        def good(case, other):
            # Across arms, only the checks both have: the control arm has no right_metrics.
            if not case or not arms or not other:
                return bool(case and case["passed"])
            common = set(case["scores"]) & set(other["scores"])
            return bool(common) and all(case["scores"][k]["value"] for k in common)

        rows, change = [], {"fixed": 0, "broken": 0, "same": 0, "no_answer": 0}
        for name in sorted(before.keys() | after.keys()):
            b, a = before.get(name), after.get(name)
            # A question that got no answer says nothing about the agent: neither fixed nor broken.
            state = ("no_answer" if (a and a["error"]) or (b and b["error"]) else
                     "fixed" if good(a, b) and not good(b, a) else
                     "broken" if good(b, a) and not good(a, b) else "same")
            change[state] += 1
            rows.append({"name": name, "question": (a or b)["question"], "change": state,
                         "baseline": {k: b[k] for k in ("passed", "scores", "answer")} if b else None,
                         "candidate": {k: a[k] for k in ("passed", "scores", "answer")} if a else None})
        reliable = all(r["status"] == "done" and r["summary"].get("reliable", True) for r in (base, cand))
        # The counts are fields, so camelCase; a case's `change` is a value and stays `no_answer`.
        result = {"baseline": self.public_run(base), "candidate": self.public_run(cand), "reliable": reliable,
                  "change": {"fixed": change["fixed"], "broken": change["broken"], "same": change["same"],
                             "noAnswer": change["no_answer"]}, "cases": rows}
        if arms and reliable:
            result["uplift"] = uplift(before, after, base["arm"], good)
        return result

    # Schedules

    @staticmethod
    def next_run(cron, check=True):
        """When a cron expression fires next. A new one has five fields and fires at most every 5 minutes."""
        now = time.time()
        if check:
            if not croniter.is_valid(cron) or len(cron.split()) != 5:
                raise EvalError(422, "Use a five-field cron expression in UTC, such as '0 6 * * 1'.", "invalid_cron")
            times = croniter(cron, now)
            first, second = times.get_next(float), times.get_next(float)
            if second - first < 300:
                raise EvalError(422, "Schedules run at most every 5 minutes.", "invalid_cron")
        return croniter(cron, now).get_next(float)

    async def list_schedules(self, caller, id):
        await self.question_set_for(caller, id)
        return {"schedules": [self.public_schedule(s) for s in self.store.schedules(id)]}

    async def create_schedule(self, caller, id, cron, llm_model="", enabled=True):
        _, me, _ = await self.question_set_for(caller, id, "admin")
        return self.public_schedule(self.store.create_schedule(id, cron, llm_model or "", self.next_run(cron),
                                                               self.by(caller, me), enabled))

    async def update_schedule(self, caller, schedule_id, data):
        """Changes what is given and keeps the rest."""
        schedule = await self.schedule_for(caller, schedule_id)
        fields = {column: data[key] for key, column in (("cron", "cron"), ("llmModel", "llm_model"))
                  if data.get(key) is not None}
        if data.get("enabled") is not None:
            fields["enabled"] = int(data["enabled"])
        # Counted from now, so a schedule that was off does not start a run for a time that has passed.
        fields["next_run_at"] = self.next_run(fields.get("cron", schedule["cron"]), check="cron" in fields)
        self.store.update_schedule(schedule_id, **fields)
        return self.public_schedule(self.store.schedule(schedule_id))

    async def delete_schedule(self, caller, schedule_id):
        await self.schedule_for(caller, schedule_id)
        self.store.delete_schedule(schedule_id)
        return {"deleted": True, "schedule": schedule_id}

    # Live evaluation and reviews

    async def member(self, caller, team, minimum="reader"):
        me = await self.me(caller)
        role = self.role(me, team)
        if not role:
            raise EvalError(403, "You are not a member of this team.", "not_a_member")
        if (minimum == "writer" and role not in WRITERS) or (minimum == "admin" and role not in ADMINS):
            who = "administrator" if minimum == "admin" else "writer or administrator"
            raise EvalError(403, f"This needs the {who} role in the team.", "forbidden_role")
        return me, role

    async def get_live_evaluation(self, caller, team, environment):
        await self.member(caller, team)
        return self.public_live(team, environment, self.store.live(team, environment))

    async def update_live_evaluation(self, caller, team, environment, data):
        """Changes what is given and keeps the rest; a team environment nobody set up starts from the defaults."""
        me, _ = await self.member(caller, team, "admin")
        current = self.store.live(team, environment) or LIVE_DEFAULTS
        merged = {column: data[key] if data.get(key) is not None else current[column] for key, column in (
            ("enabled", "enabled"), ("samplePct", "sample_pct"), ("cron", "cron"),
            ("targetDatabase", "target_database"))}
        enabled = bool(merged["enabled"])
        next_run = self.next_run(merged["cron"], check=data.get("cron") is not None)
        if enabled:
            await self.bridge.principal_for(team, environment)
        # Conversational BI records only while this is on, so it hears first: no setting says on while it keeps
        # nothing, or off while it still records.
        await self.agent.recording(team, environment, enabled)
        setting = self.store.set_live(team, environment, enabled, merged["sample_pct"], merged["cron"], next_run,
                                      merged["target_database"], self.by(caller, me))
        note = ("Conversational BI keeps this team's conversations here from now on, for its retention period."
                if enabled else
                "Conversational BI keeps none of this team's conversations here, and deleted the ones it kept.")
        return {**self.public_live(team, environment, setting), "note": note}

    async def list_reviews(self, caller, team, environment, status=None, limit=100):
        await self.member(caller, team)
        return {"reviews": [self.public_review(r) for r in self.store.reviews(team, environment, status, limit)]}

    async def review_for(self, caller, review_id, minimum="writer"):
        review = self.store.review(review_id)
        me = await self.me(caller)
        if review is None or not self.role(me, review["team"]):
            raise EvalError(404, "No such review in your teams.", "unknown_review")
        await self.member(caller, review["team"], minimum)
        return review, me

    async def update_review(self, caller, review_id, status, note=None):
        """Dismiss a review (`dismissed`) or put it back in the queue (`open`); the note stays unless one is given."""
        review, me = await self.review_for(caller, review_id)
        self.store.update_review(review_id, status=status, note=note if note is not None else review["note"],
                                 reviewed_by=self.by(caller, me), reviewed_at=time.time())
        return self.public_review(self.store.review(review_id))

    async def promote_review(self, caller, review_id, question_set_id, name=None, expect=None):
        """A real question becomes a case; by default with the query the agent ran, as a reviewer approved it."""
        review, me = await self.review_for(caller, review_id)
        found, _, role = await self.question_set_for(caller, question_set_id, "edit")
        if (found["team"], found["environment"]) != (review["team"], review["environment"]):
            raise EvalError(422, "Promote to a question set of the same team and environment.", "invalid_request")
        if expect is None:
            ran = [q for q in review["queries"] if "error" not in q]
            if not ran:
                raise EvalError(422, "The agent ran no successful query here: say what the expected outcome is "
                                     "(expect).", "expect_required")
            if any(review["model"].get(k) != found["model"][k] for k in MODEL_KEYS):
                raise EvalError(422, "This conversation used another semantic model than the question set.",
                                "other_model")
            spec = {k: v for k, v in ran[-1]["spec"].items() if k in ("metrics", "dimensions", "filters",
                                                                      "order_by", "limit") and v}
            expect = {"query": spec}
        current = questions.parse(self.store.version(found["id"], found["version"])["yaml"])
        taken = {c.name for c in current}
        try:
            case = questions.Case(name=name or questions.slug(review["question"], set(taken)),
                                  question=review["question"], expect=expect, tags=["live"])
        except ValueError as exc:
            raise EvalError(422, f"This case is not valid: {questions.problem(exc)}", "invalid_question_set") from None
        if case.name in taken:
            raise EvalError(409, "The question set already has a case with this name.", "name_taken")
        cases = [*current, case]
        self.store.update_set(found["id"], self.by(caller, me), yaml=questions.dump(cases, found["name"]),
                              cases=len(cases), note=f"promoted from live review {review_id}")
        self.store.update_review(review_id, status="promoted", promoted_to=f"{found['id']}:{case.name}",
                                 reviewed_by=self.by(caller, me), reviewed_at=time.time())
        return {"review": self.public_review(self.store.review(review_id)), "case": case.name,
                "questionSet": self.detail(self.store.question_set(found["id"]), role)}
