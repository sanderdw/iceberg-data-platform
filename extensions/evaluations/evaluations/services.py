"""Every capability of the Evaluations extension, shared by the REST API and the MCP server.

Authorization mirrors the platform's team roles, read from the Bridge on every call:

| Role in the owning team | Allowed |
| --- | --- |
| reader | View question sets, runs, results and schedules |
| writer | Also create and edit question sets and start runs in development |
| admin, bucket-admin | Also enable environments, work in acceptance and production, schedule, run live evaluation |

Reviewing live conversations (dismiss, promote to a case) needs the writer role; the question set a
case goes to follows its own rules.

Runs ask the agent for the team, never as the person who started them: Conversational BI answers
only where a team administrator enabled both extensions, with the scope every team member has.
"""

import time

from croniter import croniter

from . import questions
from .config import AGENT, ENVIRONMENTS
from .errors import EvalError
from .store import new_id

WRITERS = ("writer", "admin", "bucket-admin")
ADMINS = ("admin", "bucket-admin")
MAX_COMPARE = 5


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


def public_set(item, role=None):
    return {**item, "role": role}


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

    async def run_for(self, caller, run_id):
        run = self.store.run(run_id)
        if run is None:
            raise EvalError(404, "No such run in your teams.", "unknown_run")
        found, _, _ = await self.question_set_for(caller, run["question_set"])
        return run, found

    @staticmethod
    def by(caller, me):
        return me["user"]["name"] + (" (via agent)" if caller.agent else "")

    # Discovery and environments

    async def enabled(self, caller, team):
        return sorted({p["environment"] for p in await self.bridge.team_automation(caller.token, team)},
                      key=ENVIRONMENTS.index)

    async def whoami(self, caller):
        me = await self.me(caller)
        discovery = await self.bridge.discovery()
        return {**me, "extension": self.settings.extension_id, "origin": self.settings.origin,
                "environments": list(ENVIRONMENTS), "agent": AGENT,
                "judge": bool(self.settings.judge_model),
                "enabledEnvironments": [{"team": m["team"], "environments": await self.enabled(caller, m["team"])}
                                        for m in me["memberships"]],
                "userPortalUrl": discovery.get("userPortalUrl", "")}

    async def environments(self, caller, team):
        me = await self.me(caller)
        if not self.role(me, team):
            raise EvalError(403, "You are not a member of this team.", "not_a_member")
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        return [{"environment": env, "enabled": env in enabled,
                 **({"enabledBy": enabled[env]["createdBy"]} if env in enabled else {})} for env in ENVIRONMENTS]

    async def enable_environment(self, caller, team, environment):
        if environment not in ENVIRONMENTS:
            raise EvalError(422, "Choose development, acceptance or production.", "invalid_request")
        return await self.bridge.enable(caller.token, team, environment)

    async def disable_environment(self, caller, team, environment):
        enabled = {p["environment"]: p for p in await self.bridge.team_automation(caller.token, team)}
        if environment not in enabled:
            raise EvalError(404, "Evaluations is not enabled for this environment.", "environment_not_enabled")
        await self.bridge.revoke(caller.token, enabled[environment]["id"])
        return {"disabled": True, "environment": environment}

    async def agent_models(self, caller, team, environment):
        """The semantic models the agent can answer from for this team, and the LLMs a run may choose."""
        me = await self.me(caller)
        if not self.role(me, team):
            raise EvalError(403, "You are not a member of this team.", "not_a_member")
        await self.bridge.principal_for(team, environment)
        return await self.agent.models(team, environment)

    # Question sets

    async def list_question_sets(self, caller):
        me = await self.me(caller)
        roles = {m["team"]: m["role"] for m in me["memberships"]}
        items = []
        for item in self.store.question_sets(list(roles)):
            last = self.store.runs(item["id"], limit=1)
            items.append({**public_set(item, roles[item["team"]]), "lastRun": last[0] if last else None})
        return {"questionSets": items}

    async def get_question_set(self, caller, id, version=None):
        found, _, role = await self.question_set_for(caller, id)
        stored = self.store.version(id, version or found["version"])
        if stored is None:
            raise EvalError(404, "No such version.", "unknown_version")
        return {**public_set(found, role), "yaml": stored["yaml"], "shownVersion": stored["version"],
                "versions": self.store.versions(id), "schedules": self.store.schedules(id),
                "runs": self.store.runs(id, limit=10)}

    async def save_question_set(self, caller, data):
        """Create one (no id) or change one; new YAML is a new version."""
        cases = questions.parse(data.yaml) if data.yaml is not None else None
        if data.id is None:
            me = await self.me(caller)
            self.needs(self.role(me, data.team), data.environment, "edit")
            if not data.model or not cases:
                raise EvalError(422, "A new question set needs a semantic model and at least one case.",
                                "invalid_request")
            if any(s["name"] == data.name and s["environment"] == data.environment
                   for s in self.store.question_sets([data.team])):
                raise EvalError(409, "This team already has a question set with this name there.", "name_taken")
            created = self.store.create_set(
                data.team, data.environment, data.name, AGENT, data.model.model_dump(),
                questions.dump(cases, data.name), len(cases), self.by(caller, me),
                description=data.description or "", rubric=data.rubric or "",
                target_database=data.target_database or "")
            return await self.get_question_set(caller, created["id"])
        found, me, _ = await self.question_set_for(caller, data.id, "edit")
        fields = {k: v for k, v in (("name", data.name), ("description", data.description),
                                    ("rubric", data.rubric), ("target_database", data.target_database),
                                    ("model", data.model.model_dump() if data.model else None)) if v is not None}
        if cases is not None and not cases:
            raise EvalError(422, "A question set needs at least one case.", "invalid_request")
        yaml = questions.dump(cases, data.name or found["name"]) if cases else None
        self.store.update_set(found["id"], self.by(caller, me), yaml=yaml, cases=len(cases) if cases else None,
                              note=data.note or "", **fields)
        return await self.get_question_set(caller, found["id"])

    async def import_questions(self, caller, id, text):
        """Cases from a semantic model's <name>.questions.sql; same names are replaced, others added."""
        found, me, _ = await self.question_set_for(caller, id, "edit")
        imported = questions.from_questions_sql(text)
        current = questions.parse(self.store.version(id, found["version"])["yaml"])
        merged = {c.name: c for c in current} | {c.name: c for c in imported}
        if len(merged) > questions.MAX_CASES:
            raise EvalError(422, f"A question set has at most {questions.MAX_CASES} cases.", "invalid_question_set")
        cases = list(merged.values())
        self.store.update_set(id, self.by(caller, me), yaml=questions.dump(cases, found["name"]), cases=len(cases),
                              note=f"imported {len(imported)} questions")
        return {**await self.get_question_set(caller, id), "imported": len(imported)}

    async def delete_question_set(self, caller, id):
        found, _, _ = await self.question_set_for(caller, id, "edit")
        if any(r["status"] in ("queued", "running") for r in self.store.runs(id, limit=20)):
            raise EvalError(409, "A run of this question set is still going.", "run_active")
        self.store.delete_set(found["id"])
        return {"deleted": True}

    # Runs

    async def start(self, question_set, triggered_by, llm_model="", schedule="", arm="semantic", pair=""):
        await self.bridge.principal_for(question_set["team"], question_set["environment"])
        run = self.store.create_run(question_set["id"], triggered_by, llm_model=llm_model, schedule=schedule, arm=arm,
                                    pair=pair)
        self.runner.start(run)
        return run

    async def run_evaluation(self, caller, id, llm_model=""):
        found, me, _ = await self.question_set_for(caller, id, "run")
        return await self.start(found, self.by(caller, me), llm_model or "")

    async def compare_models(self, caller, id, models):
        found, me, _ = await self.question_set_for(caller, id, "run")
        models = list(dict.fromkeys(m for m in models if m))
        if not 2 <= len(models) <= MAX_COMPARE:
            raise EvalError(422, f"Compare 2 to {MAX_COMPARE} models.", "invalid_request")
        runs = [await self.start(found, self.by(caller, me), m) for m in models]
        return {"runs": runs}

    async def measure_semantic_model(self, caller, id, llm_model=""):
        """The same questions and LLM twice: with the semantic model, and with only the table schemas."""
        found, me, _ = await self.question_set_for(caller, id, "run")
        await self.bridge.principal_for(found["team"], found["environment"])
        if not (await self.agent.models(found["team"], found["environment"])).get("schemaOnly"):
            raise EvalError(409, "The control arm is off: a platform administrator sets BI_EVAL_SCHEMA_ONLY=true in "
                                 "Conversational BI, because there an LLM writes SQL.", "schema_only_disabled")
        pair = new_id("pair")
        by = self.by(caller, me)
        return {"pair": pair, "control": await self.start(found, by, llm_model or "", arm="control", pair=pair),
                "semantic": await self.start(found, by, llm_model or "", pair=pair)}

    async def list_runs(self, caller, id):
        await self.question_set_for(caller, id)
        return {"runs": self.store.runs(id)}

    async def get_run(self, caller, run_id):
        run, found = await self.run_for(caller, run_id)
        return {**run, "questionSetName": found["name"], "cases": self.store.cases(run_id)}

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
        result = {"baseline": base, "candidate": cand, "reliable": reliable, "change": change, "cases": rows}
        if arms and reliable:
            result["uplift"] = uplift(before, after, base["arm"], good)
        return result

    # Schedules

    async def set_schedule(self, caller, id, cron, llm_model="", enabled=True, schedule_id=None):
        _, me, _ = await self.question_set_for(caller, id, "admin")
        if not croniter.is_valid(cron) or len(cron.split()) != 5:
            raise EvalError(422, "Use a five-field cron expression in UTC, such as '0 6 * * 1'.", "invalid_cron")
        next_run = croniter(cron, time.time()).get_next(float)
        if schedule_id:
            existing = self.store.schedule(schedule_id)
            if not existing or existing["question_set"] != id:
                raise EvalError(404, "No such schedule.", "unknown_schedule")
            self.store.update_schedule(schedule_id, cron=cron, llm_model=llm_model, enabled=int(enabled),
                                       next_run_at=next_run)
            return self.store.schedule(schedule_id)
        return self.store.create_schedule(id, cron, llm_model, next_run, self.by(caller, me))

    async def delete_schedule(self, caller, id, schedule_id):
        await self.question_set_for(caller, id, "admin")
        existing = self.store.schedule(schedule_id)
        if not existing or existing["question_set"] != id:
            raise EvalError(404, "No such schedule.", "unknown_schedule")
        self.store.delete_schedule(schedule_id)
        return {"deleted": True}

    # Live evaluation and reviews

    async def member(self, caller, team, minimum="reader"):
        me = await self.me(caller)
        role = self.role(me, team)
        if not role:
            raise EvalError(403, "You are not a member of this team.", "not_a_member")
        if (minimum == "writer" and role not in WRITERS) or (minimum == "admin" and role not in ADMINS):
            raise EvalError(403, f"This needs the {minimum} role in the team.", "forbidden_role")
        return me, role

    async def get_live(self, caller, team, environment):
        await self.member(caller, team)
        setting = self.store.live(team, environment) or {"team": team, "environment": environment, "enabled": False,
                                                          "sample_pct": 20, "cron": "0 * * * *", "target_database": ""}
        return {**setting, "openReviews": len(self.store.reviews(team, environment, "open", limit=500))}

    async def set_live(self, caller, team, environment, enabled, sample_pct, cron, target_database):
        me, _ = await self.member(caller, team, "admin")
        if not croniter.is_valid(cron) or len(cron.split()) != 5:
            raise EvalError(422, "Use a five-field cron expression in UTC, such as '0 * * * *'.", "invalid_cron")
        if enabled:
            await self.bridge.principal_for(team, environment)
        setting = self.store.set_live(team, environment, enabled, sample_pct, cron,
                                      croniter(cron, time.time()).get_next(float), target_database or "",
                                      self.by(caller, me))
        return {**setting, "note": "Conversational BI keeps this team's conversations while Evaluations is enabled "
                                   "here, for its retention period."}

    async def list_reviews(self, caller, team, environment, status=None):
        await self.member(caller, team)
        return {"reviews": self.store.reviews(team, environment, status)}

    async def review_for(self, caller, review_id, minimum="writer"):
        review = self.store.review(review_id)
        if review is None:
            raise EvalError(404, "No such review in your teams.", "unknown_review")
        me, _ = await self.member(caller, review["team"], minimum)
        return review, me

    async def review_conversation(self, caller, review_id, action, note=""):
        review, me = await self.review_for(caller, review_id)
        status = {"dismiss": "dismissed", "reopen": "open"}[action]
        self.store.update_review(review_id, status=status, note=note or review["note"], reviewed_by=self.by(caller, me),
                                 reviewed_at=time.time())
        return self.store.review(review_id)

    async def promote_to_case(self, caller, review_id, question_set_id, name=None, expect=None):
        """A real question becomes a case; by default with the query the agent ran, as a reviewer approved it."""
        review, me = await self.review_for(caller, review_id)
        found, _, _ = await self.question_set_for(caller, question_set_id, "edit")
        if (found["team"], found["environment"]) != (review["team"], review["environment"]):
            raise EvalError(422, "Promote to a question set of the same team and environment.", "invalid_request")
        if expect is None:
            ran = [q for q in review["queries"] if "error" not in q]
            if not ran:
                raise EvalError(422, "The agent ran no successful query here: say what the expected outcome is "
                                     "(expect).", "expect_required")
            if review["model"] != found["model"]:
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
        return {"review": self.store.review(review_id), "case": case.name,
                "questionSet": await self.get_question_set(caller, found["id"])}

