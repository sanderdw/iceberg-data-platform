"""How an answer is scored. Most checks are code; an LLM judge only reads tone and clarity.

The task's output is `{"agent": <Conversational BI's answer>, "truth": <the expected query's result>}`:

- result_matches: one of the agent's results has the expected query's rows (in order when it orders);
  agents often run a few queries before they answer.
- right_metrics: one of the agent's queries asked for at least the expected metrics.
- grounded: every number in the answer comes from a query result, or from the question.
- refused: for an expected refusal, the agent ran into that code and stated no figures.
- clarified: for a vague question, the agent asked back instead of querying.
- declined: for a question the model can't answer, no query and no figures.
- judge: an LLM grades the answer against the rubric (only with JUDGE_MODEL).

A scorer that does not apply to a case reports nothing, so pass rates count only real checks. The control
arm (an LLM with only the table schemas, writing SQL) has no governed spec and no refusal codes, so
right_metrics doesn't apply to it and refused only asks for no figures.
"""

import json
import re
from dataclasses import dataclass

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?(?:Z|[+-]00:?00)?$")
NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w])")


def cell(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(f"{float(value):.6g}")
    text = str(value).strip()
    # A day grain gives midnight timestamps, a DATE column plain dates: the same day.
    found = MIDNIGHT.match(text)
    return found.group(1) if found else text


def same(value, expected):
    """Equal, or a number the agent rounded itself: SQL such as ROUND(x, 2) is compared at its own precision."""
    if cell(value) == cell(expected):
        return True
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (value, expected)):
        return False
    written = repr(float(value))
    decimals = len(written.split(".", 1)[1].rstrip("0")) if "." in written and "e" not in written else 0
    return decimals >= 1 and abs(round(float(expected), decimals) - float(value)) < 1e-9


def contains(row, expected):
    """The agent's row holds every value of the expected row (it may add columns)."""
    left = list(row)
    for want in expected:
        hit = next((i for i, value in enumerate(left) if same(value, want)), None)
        if hit is None:
            return False
        del left[hit]
    return True


def results(agent):
    return [q for q in agent.get("queries", []) if "error" not in q and q.get("rows") is not None]


def codes(agent):
    found = set()
    for call in agent.get("toolCalls", []):
        result = call.get("result")
        if isinstance(result, dict) and result.get("code"):
            found.add(result["code"])
    return found


def numbers(text):
    """(value, decimals) of every number written in the text; 1,234.5 and 16,9 both read right."""
    found = []
    for match in NUMBER.finditer(text or ""):
        raw = match.group()
        if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            raw = raw.replace(",", "")
        raw = raw.replace(",", ".")
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        found.append((float(raw), decimals))
    return found


def incidental(value, decimals):
    """Small counts and years ('top 3', 'in 2024') are not figures from the data."""
    return decimals == 0 and (0 <= value <= 10 or 1900 <= value <= 2100)


def sources(agent, question):
    """Every number the agent saw: the question, result cells and counts, and the text of every tool result
    (metric definitions such as 'less than 15 minutes', the SQL, dates such as 2026-01-29)."""
    values = [n for n, _ in numbers(question)]
    for q in results(agent):
        values.append(float(q.get("rowCount") or 0))
        for row in q["rows"]:
            values += [float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)]
    for call in agent.get("toolCalls", []):
        text = json.dumps(call.get("result"), default=str) + " " + json.dumps(call.get("args"), default=str)
        # Any digit run: the 30 in 2026-01-30T00:00:00 counts too.
        values += [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    return values


def grounded_in(value, decimals, values):
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    # A fraction may be written as a percentage (0.0245 as 2.45).
    return any(abs(v - value) <= tolerance or (abs(v) <= 1 and abs(v * 100 - value) <= tolerance) for v in values)


def figures(answer):
    return [(n, d) for n, d in numbers(answer) if not incidental(n, d)]


def grounded_check(agent, question):
    """(passed, reason): every figure in the answer comes from a query result or the question."""
    values = sources(agent, question)
    loose = [n for n, d in figures(agent.get("answer", "")) if not grounded_in(n, d, values)]
    return (not loose, "Every figure comes from a result." if not loose
            else "Not in any result: " + ", ".join(f"{n:g}" for n in loose[:5]) + ".")


def failed_queries_check(agent):
    """(passed, reason): a refusal the agent got is explained, not answered around with figures."""
    refused = codes(agent)
    if not refused or results(agent):
        return True, "No unanswered refusal."
    stated = figures(agent.get("answer", ""))
    return (not stated, "Explained the refusal without figures." if not stated
            else f"After {', '.join(sorted(refused))} it still states figures.")


def matches(found, truth, query):
    """(passed, reason) of one agent result against the expected result."""
    shown, rows = found["rows"], truth.get("rows", [])
    if query.get("order_by"):
        k = min(len(shown), len(rows), query.get("limit") or len(rows))
        if k == 0:
            return len(shown) == len(rows), "Both results are empty." if not rows else "The result is empty."
        wrong = next((i for i in range(k) if not contains(shown[i], rows[i])), None)
        return (wrong is None, f"The first {k} rows match, in order." if wrong is None
                else f"Row {wrong + 1} differs: {shown[wrong]} instead of {rows[wrong]}.")
    if found.get("rowCount") != truth.get("rowCount"):
        return False, f"{found.get('rowCount')} rows instead of {truth.get('rowCount')}."
    missing = [r for r in shown if not any(contains(r, t) for t in rows)]
    return (not missing, "Every row matches." if not missing
            else f"{len(missing)} rows are not in the expected result, such as {missing[0]}.")


@dataclass
class ResultMatches(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        expect = ctx.expected_output or {}
        if "query" not in expect:
            return {}
        truth, agent = ctx.output.get("truth") or {}, ctx.output["agent"]
        if "error" in truth:
            return EvaluationReason(False, f"The expected query itself fails ({truth.get('code')}): fix the case.")
        found = results(agent)
        if not found:
            return EvaluationReason(False, "The agent ran no successful query.")
        checked = [matches(q, truth, expect["query"]) for q in found]
        hit = next((i for i, (ok, _) in enumerate(checked) if ok), None)
        if hit is not None:
            return EvaluationReason(True, checked[hit][1] + (f" (query {hit + 1} of {len(found)})"
                                                             if len(found) > 1 else ""))
        return EvaluationReason(False, checked[-1][1] + (f" No query of {len(found)} matches." if len(found) > 1
                                                         else ""))


@dataclass
class RightMetrics(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        expect = ctx.expected_output or {}
        if "query" not in expect or ctx.output.get("arm") == "control":
            return {}
        wanted = set(expect["query"]["metrics"])
        asked = [set(q["spec"].get("metrics", [])) for q in results(ctx.output["agent"])]
        if any(wanted <= a for a in asked):
            return EvaluationReason(True, "The expected metrics.")
        missing = sorted(wanted - set().union(*asked)) if asked else sorted(wanted)
        return EvaluationReason(False, f"Missing: {', '.join(missing)}." if missing
                                else "The expected metrics, but never in one query.")


@dataclass
class Grounded(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        return EvaluationReason(*grounded_check(ctx.output["agent"], ctx.inputs.get("question", "")))


@dataclass
class Refused(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        expect = ctx.expected_output or {}
        if "refusal" not in expect:
            return {}
        agent = ctx.output["agent"]
        if ctx.output.get("arm") != "control" and expect["refusal"] not in codes(agent):
            return EvaluationReason(False, f"The agent never ran into {expect['refusal']}.")
        stated = figures(agent.get("answer", ""))
        return EvaluationReason(not stated, "Explained without figures." if not stated
                                else "It still states figures: " + ", ".join(f"{n:g}" for n, _ in stated[:3]) + ".")


@dataclass
class Clarified(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        expect = ctx.expected_output or {}
        if not expect.get("clarify"):
            return {}
        agent = ctx.output["agent"]
        if results(agent):
            return EvaluationReason(False, "The agent answered with a query instead of asking back.")
        asked = "?" in agent.get("answer", "")
        return EvaluationReason(asked, "Asked back." if asked else "Neither a query nor a question back.")


@dataclass
class Declined(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        expect = ctx.expected_output or {}
        if not expect.get("decline"):
            return {}
        agent = ctx.output["agent"]
        if results(agent):
            return EvaluationReason(False, "The agent ran a query for a question the model can't answer.")
        stated = figures(agent.get("answer", ""))
        return EvaluationReason(not stated, "Declined without figures." if not stated
                                else "It states figures anyway: " + ", ".join(f"{n:g}" for n, _ in stated[:3]) + ".")


@dataclass
class Judge(Evaluator):
    rubric: str = ""
    model: str = ""

    async def evaluate(self, ctx: EvaluatorContext):
        from pydantic_evals.evaluators.llm_as_a_judge import judge_input_output

        rubric = " ".join(r for r in (self.rubric, (ctx.metadata or {}).get("rubric", "")) if r)
        if not rubric or not self.model:
            return {}
        graded = await judge_input_output(ctx.inputs.get("question", ""), ctx.output["agent"].get("answer", ""),
                                          rubric, self.model)
        return EvaluationReason(graded.pass_, graded.reason)


def scorers(judge_model="", rubric=""):
    found = [ResultMatches(), RightMetrics(), Grounded(), Refused(), Clarified(), Declined()]
    if judge_model:
        found.append(Judge(rubric=rubric, model=judge_model))
    return found


NAMES = {"ResultMatches": "result_matches", "RightMetrics": "right_metrics", "Grounded": "grounded",
         "Refused": "refused", "Clarified": "clarified", "Declined": "declined", "Judge": "judge"}
