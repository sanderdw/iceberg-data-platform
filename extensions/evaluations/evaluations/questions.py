"""Question sets: cases with a known answer, as pydantic-evals YAML.

A case asks one question and says what a good answer is:

- `query`: the governed query that answers it (metrics, dimensions, filters, order_by, limit). Its
  result is computed again at every run, so a case stays right when the data changes.
- `refusal`: the error code the agent must run into and explain instead of answering with numbers,
  such as `ambiguous_join`, `fan_out` or `unknown_metric`.
- `clarify`: the question is too vague; the agent should ask back instead of guessing.
- `decline`: the model can't answer it (no such data, a forecast, an instruction to break the
  rules); the agent should say so, without running a query or stating figures.

The YAML is the pydantic-evals dataset format, so `Dataset.from_text` reads it too:

    cases:
    - name: delay_by_carrier
      inputs:
        question: Which carrier has the highest average departure delay?
      expected_output:
        query: {metrics: [average_departure_delay], dimensions: [{field: CARRIER.name}]}
      metadata: {tags: [ranking]}
"""

import json
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import EvalError

NAME = r"^[a-z0-9][a-z0-9_-]{0,59}$"
MAX_CASES = 200


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any] | None = None
    refusal: str | None = Field(default=None, pattern=r"^[a-z][a-z_]{2,39}$")
    clarify: bool = False
    decline: bool = False

    @model_validator(mode="after")
    def one(self):
        if sum((self.query is not None, self.refusal is not None, self.clarify, self.decline)) != 1:
            raise ValueError("expected_output needs exactly one of query, refusal, clarify or decline")
        if self.query is not None:
            metrics = self.query.get("metrics")
            if not isinstance(metrics, list) or not 1 <= len(metrics) <= 5 or not all(
                    isinstance(m, str) for m in metrics):
                raise ValueError("query needs 1 to 5 metric names")
            unknown = set(self.query) - {"metrics", "dimensions", "filters", "order_by", "limit"}
            if unknown:
                raise ValueError(f"query has unknown keys: {', '.join(sorted(unknown))}")
        return self

    @property
    def kind(self):
        if self.query is not None:
            return "query"
        return "refusal" if self.refusal else "clarify" if self.clarify else "decline"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME)
    question: str = Field(min_length=1, max_length=2000)
    expect: Expect
    tags: list[str] = Field(default=[], max_length=10)
    # What the LLM judge looks for in this answer, on top of the question set's rubric.
    rubric: str = Field(default="", max_length=1000)


def problem(exc):
    first = exc.errors()[0] if isinstance(exc, ValidationError) and exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    return f"{where}: {first.get('msg', exc)}" if where else str(first.get("msg", exc))


def parse(text):
    """YAML → cases, or a readable reason why not."""
    try:
        data = yaml.safe_load(text) if text and text.strip() else {"cases": []}
    except yaml.YAMLError as exc:
        raise EvalError(422, f"This is not valid YAML: {str(exc).splitlines()[0]}", "invalid_yaml") from None
    if not isinstance(data, dict) or not isinstance(data.get("cases", []), list):
        raise EvalError(422, "The YAML needs a top-level `cases:` list.", "invalid_question_set")
    cases, names = [], set()
    for i, item in enumerate(data.get("cases") or []):
        if not isinstance(item, dict):
            raise EvalError(422, f"Case {i + 1} is not a mapping.", "invalid_question_set")
        inputs, metadata = item.get("inputs") or {}, item.get("metadata") or {}
        try:
            case = Case(name=item.get("name") or f"case_{i + 1}",
                        question=inputs.get("question", "") if isinstance(inputs, dict) else str(inputs),
                        expect=item.get("expected_output") or {}, tags=metadata.get("tags", []),
                        rubric=metadata.get("rubric", ""))
        except ValidationError as exc:
            raise EvalError(422, f"Case {item.get('name') or i + 1}: {problem(exc)}", "invalid_question_set") from None
        if case.name in names:
            raise EvalError(422, f"Two cases are named {case.name}.", "invalid_question_set")
        names.add(case.name)
        cases.append(case)
    if len(cases) > MAX_CASES:
        raise EvalError(422, f"A question set has at most {MAX_CASES} cases.", "invalid_question_set")
    return cases


def dump(cases, name=None):
    items = []
    for case in cases:
        expect = case.expect.model_dump(exclude_defaults=True)
        metadata = {k: v for k, v in (("tags", case.tags), ("rubric", case.rubric)) if v}
        items.append({"name": case.name, "inputs": {"question": case.question}, "expected_output": expect,
                      **({"metadata": metadata} if metadata else {})})
    return yaml.safe_dump({**({"name": name} if name else {}), "cases": items}, sort_keys=False,
                          allow_unicode=True, width=120)


def slug(question, taken):
    words = re.findall(r"[a-z0-9]+", question.lower())[:6]
    base = "_".join(words)[:50] or "question"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def from_questions_sql(text):
    """The semantic-model skill's <name>.questions.sql: `-- Q:` and `-- query_semantic_model:` per block."""
    cases, taken, question = [], set(), None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-- Q:"):
            question = stripped[5:].strip()
        elif stripped.startswith("-- query_semantic_model:") and question:
            try:
                spec = json.loads(stripped.split(":", 1)[1])
            except ValueError:
                raise EvalError(422, f"The query of '{question[:60]}' is not valid JSON.", "invalid_questions") \
                    from None
            spec.pop("model", None)
            try:
                cases.append(Case(name=slug(question, taken), question=question, expect={"query": spec},
                                  tags=["imported"]))
            except ValidationError as exc:
                raise EvalError(422, f"'{question[:60]}': {problem(exc)}", "invalid_questions") from None
            question = None
    if not cases:
        raise EvalError(422, "No `-- Q:` question with a `-- query_semantic_model:` line found.", "invalid_questions")
    return cases
