"""One evaluation run: every case of a question set version, asked of the agent and scored.

pydantic-evals runs the cases (at most EVAL_CONCURRENCY at once) and the scorers. Each case asks
Conversational BI the question, and computes the expected query's result the same way, at the
same moment, so a case stays right when the data changes. Runs execute in this process, at most
EVAL_MAX_RUNS at once; a restart marks unfinished runs as failed.

With OpenTelemetry on, a run is a span `evaluate <question set>` with a span per case; the case's
call to the agent nests the agent's own run under it. Every score is a `gen_ai.evaluation.result`
event on the case's span.
"""

import asyncio
import logging
import statistics
import time

from opentelemetry import context as otel_context
from opentelemetry import trace
from pydantic_evals import Case, Dataset

from . import questions
from .errors import EvalError
from .scorers import NAMES, scorers

LOG = logging.getLogger(__name__)
# Score events go to OpenTelemetry only (__main__.telemetry adds the handler), not to the container log.
SCORES = logging.getLogger("evaluations.scores")
SCORES.propagate = False
SCORES.setLevel(logging.INFO)
TRACER = trace.get_tracer("evaluations")
# Above this share of questions without an answer a run measures the outage, not the agent: a
# rate-limited run scores near zero and looks just like a weak model.
MAX_NO_ANSWER = 0.05


def summarize(cases):
    scorer = {}
    for case in cases:
        for name, score in case["scores"].items():
            entry = scorer.setdefault(name, {"passed": 0, "total": 0})
            entry["total"] += 1
            entry["passed"] += bool(score["value"])
    durations = sorted(c["duration_ms"] for c in cases if not c["error"])
    passed = sum(c["passed"] for c in cases)
    errors = sum(bool(c["error"]) for c in cases)
    answered = len(cases) - errors
    reliable = bool(answered) and errors / len(cases) <= MAX_NO_ANSWER
    return {
        "cases": len(cases), "answered": answered, "passed": passed, "failed": answered - passed, "errors": errors,
        "reliable": reliable, "passRate": round(passed / answered, 4) if reliable else None, "scorers": scorer,
        "inputTokens": sum(c["input_tokens"] for c in cases), "outputTokens": sum(c["output_tokens"] for c in cases),
        "medianSeconds": round(statistics.median(durations) / 1000, 2) if durations else None,
        "p95Seconds": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))] / 1000, 2)
        if durations else None,
        "models": sorted({c["model"] for c in cases if c["model"]}),
    }


def emit_scores(case, span_context):
    """`gen_ai.evaluation.result` events on the case's span (OpenTelemetry GenAI conventions)."""
    if span_context is None:
        return
    token = otel_context.attach(trace.set_span_in_context(trace.NonRecordingSpan(span_context)))
    try:
        for name, score in case["scores"].items():
            SCORES.info("%s %s: %s", case["name"], name, "pass" if score["value"] else "fail", extra={
                "otel.event.name": "gen_ai.evaluation.result", "gen_ai.evaluation.name": name,
                "gen_ai.evaluation.score.value": 1.0 if score["value"] else 0.0,
                "gen_ai.evaluation.score.label": "pass" if score["value"] else "fail",
                "gen_ai.evaluation.explanation": score.get("reason") or ""})
    finally:
        otel_context.detach(token)


class Runner:
    def __init__(self, settings, store, agent, publish=None):
        self.settings = settings
        self.store = store
        self.agent = agent
        # Writes a finished run to the team's Iceberg tables (lakehouse.py); None in tests.
        self.publish = publish
        self.slots = asyncio.Semaphore(settings.max_runs)
        self.tasks = set()

    def recover(self):
        for run in self.store.active_runs():
            self.store.update_run(run["id"], status="error", finished_at=time.time(),
                                  error="The Evaluations extension restarted during this run. Start it again.")

    def start(self, run):
        task = asyncio.create_task(self.execute(run["id"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self):
        for task in list(self.tasks):
            task.cancel()

    def dataset(self, question_set, cases):
        return Dataset(name=question_set["name"], cases=[
            Case(name=c.name, inputs={"question": c.question, "query": c.expect.query},
                 expected_output=c.expect.model_dump(exclude_defaults=True),
                 metadata={"kind": c.expect.kind, "tags": c.tags, "rubric": c.rubric}) for c in cases],
            evaluators=scorers(self.settings.judge_model, question_set["rubric"]))

    async def execute(self, run_id):
        async with self.slots:
            run = self.store.run(run_id)
            if run is None or run["status"] != "queued":
                return
            question_set = self.store.question_set(run["question_set"])
            self.store.update_run(run_id, status="running", started_at=time.time())
            try:
                with TRACER.start_as_current_span(f"evaluate {question_set['name']}", attributes={
                        "iceberg.evaluation.run": run_id, "iceberg.evaluation.question_set": question_set["id"],
                        "iceberg.team": run["team"], "iceberg.evaluation.arm": run["arm"],
                        "gen_ai.request.model": run["llm_model"] or ""}):
                    cases = await self.evaluate(run, question_set)
                summary = summarize(cases)
            except EvalError as exc:
                self.store.update_run(run_id, status="error", finished_at=time.time(), error=str(exc))
                return
            except Exception:
                LOG.exception("Evaluation run %s failed", run_id)
                self.store.update_run(run_id, status="error", finished_at=time.time(),
                                      error="Something went wrong. The extension's log has the details.")
                return
            finished, published = time.time(), ""
            if self.publish:
                # Done only once its results are in the team's tables too.
                done = {**self.store.run(run_id), "status": "done", "finished_at": finished, "summary": summary}
                try:
                    published = await self.publish(done, question_set, cases)
                except EvalError as exc:
                    published = f"not published: {exc}"
                except Exception:
                    LOG.exception("Publishing run %s failed", run_id)
                    published = "not published: unexpected error"
            self.store.update_run(run_id, status="done", finished_at=finished, summary=summary, published=published)
            self.store.prune(question_set["id"], self.settings.run_retention)

    async def evaluate(self, run, question_set):
        cases = questions.parse(self.store.version(question_set["id"], run["version"])["yaml"])
        team, environment, model = run["team"], run["environment"], question_set["model"]
        contexts = {}

        async def task(inputs):
            with TRACER.start_as_current_span("evaluate case", attributes={
                    "gen_ai.evaluation.case": inputs["question"][:200]}) as span:
                contexts[inputs["question"]] = span.get_span_context()
                llm = run["llm_model"] or None
                question = inputs["question"]
                answer = (await self.agent.ask_control(team, environment, model, question, llm)
                          if run["arm"] == "control" else await self.agent.ask(team, environment, question, llm))
                if not (answer.get("answer") or "").strip():
                    raise EvalError(502, "The agent gave no answer.", "no_answer")
                truth = await self.agent.truth(team, environment, model, inputs["query"]) if inputs["query"] else None
                return {"agent": answer, "truth": truth, "arm": run["arm"]}

        report = await self.dataset(question_set, cases).evaluate(
            task, name=f"{question_set['name']} {run['id']}", max_concurrency=self.settings.concurrency,
            progress=False)
        by_name = {c.name: c for c in cases}
        found = []
        for item in report.cases:
            agent = item.output["agent"]
            usage = agent.get("usage", {})
            scores = {NAMES.get(k, k): {"value": bool(v.value), "reason": v.reason or ""}
                      for k, v in item.assertions.items()}
            # A check that could not run (the judge's LLM rate-limited, say) leaves the case unscored.
            error = f"A check could not run: {item.evaluator_failures[0].error_message}"[:500] \
                if item.evaluator_failures else ""
            found.append({
                "name": item.name, "question": by_name[item.name].question, "kind": by_name[item.name].expect.kind,
                "tags": by_name[item.name].tags, "scores": scores,
                "passed": not error and bool(scores) and all(s["value"] for s in scores.values()),
                "answer": agent.get("answer", ""), "queries": agent.get("queries", []),
                "tool_calls": agent.get("toolCalls", []), "model": agent.get("model") or "",
                "input_tokens": usage.get("inputTokens") or 0, "output_tokens": usage.get("outputTokens") or 0,
                "duration_ms": agent.get("durationMs") or round(item.task_duration * 1000),
                "trace_id": agent.get("traceId") or "", "error": error})
        for failure in report.failures:
            found.append({"name": failure.name, "question": by_name[failure.name].question,
                          "kind": by_name[failure.name].expect.kind, "tags": by_name[failure.name].tags,
                          "passed": False, "scores": {}, "model": "",
                          "error": (failure.error_message or "The question failed.")[:500]})
        if found and all(c["error"] for c in found):
            # Nothing reached the agent: say why once, instead of per case.
            raise EvalError(502, found[0]["error"], "agent_failed")
        for case in found:
            self.store.add_case(run["id"], case)
            emit_scores(case, contexts.get(case["question"]))
        return [{"input_tokens": 0, "output_tokens": 0, "duration_ms": 0, "model": "", **c} for c in found]
