"""Live evaluation: real conversations, scored on a schedule, with the doubtful ones up for review.

A team administrator switches it on per team and environment (set_live). Conversational BI then
keeps that team's chat runs (its conversation record); on each check this extension reads the new
ones, scores a sample (`sample_pct`, the same conversations whatever the order) with the checks
that need no expected answer, and puts every one that fails in the review queue. A reviewer
dismisses it, or promotes it to a case of a question set: a real question becomes a regression test.

Checks: grounded (every figure comes from a result), refusal_explained (a refused query is not
answered around with figures), and with JUDGE_MODEL an LLM judge on whether the answer answers the
question. Scores also go to `evaluations.live_scores` in the target database, when one is set.
"""

import hashlib
import logging
import time

from croniter import croniter

from .errors import EvalError
from .scorers import failed_queries_check, grounded_check

LOG = logging.getLogger(__name__)
PAGE = 200
MAX_PAGES = 10
RUBRIC = ("The answer addresses the person's question directly, uses only figures from the queries the agent ran, "
          "states what was measured, and says so plainly when the data can't answer it.")


def sampled(conversation_id, pct):
    return int(hashlib.sha256(conversation_id.encode()).hexdigest()[:8], 16) % 100 < pct


class LiveEvaluator:
    def __init__(self, settings, store, agent, lakehouse=None):
        self.settings = settings
        self.store = store
        self.agent = agent
        self.lakehouse = lakehouse

    async def score(self, conversation):
        view = {"answer": conversation["answer"], "queries": conversation["queries"],
                "toolCalls": conversation["tool_calls"]}
        scores = {}
        for name, (value, reason) in (("grounded", grounded_check(view, conversation["question"])),
                                      ("refusal_explained", failed_queries_check(view))):
            scores[name] = {"value": value, "reason": reason}
        if self.settings.judge_model:
            from pydantic_evals.evaluators.llm_as_a_judge import judge_input_output

            graded = await judge_input_output(conversation["question"], conversation["answer"], RUBRIC,
                                              self.settings.judge_model)
            scores["judge"] = {"value": graded.pass_, "reason": graded.reason}
        return scores

    async def check(self, setting):
        team, environment = setting["team"], setting["environment"]
        since, found = setting["watermark"], []
        for _ in range(MAX_PAGES):
            page = (await self.agent.conversations(team, environment, since, PAGE))["conversations"]
            found += page
            if page:
                since = page[-1]["at"]
            if len(page) < PAGE:
                break
        rows, flagged = [], 0
        for conversation in found:
            if not sampled(conversation["id"], setting["sample_pct"]):
                continue
            scores = await self.score(conversation)
            passed = all(s["value"] for s in scores.values())
            if not passed:
                self.store.add_review(team, environment, conversation, scores)
                flagged += 1
            rows.append({**conversation, "scores": scores, "passed": passed})
        published = ""
        if rows and setting["target_database"] and self.lakehouse:
            try:
                published = await self.lakehouse.publish_live(team, environment, setting["target_database"], rows)
            except EvalError as exc:
                published = f"not published: {exc}"
        return {"conversations": len(found), "checked": len(rows), "flagged": flagged, "published": published}, since

    async def tick(self, now=None):
        now = now or time.time()
        checked = []
        for setting in self.store.due_live(now):
            next_run = croniter(setting["cron"], now).get_next(float)
            team, environment = setting["team"], setting["environment"]
            try:
                summary, watermark = await self.check(setting)
                self.store.update_live(team, environment, next_run_at=next_run, last_checked=now,
                                       last_summary=summary, watermark=watermark)
                checked.append((team, environment))
            except EvalError as exc:
                LOG.warning("Live evaluation of %s %s did not run: %s", team, environment, exc.code)
                self.store.update_live(team, environment, next_run_at=next_run, last_checked=now,
                                       last_summary={"error": str(exc)})
        return checked
