import pytest

from evaluations import questions
from evaluations.errors import EvalError
from tests.conftest import YAML


def test_round_trip_in_pydantic_evals_format():
    cases = questions.parse(YAML)
    assert [c.expect.kind for c in cases] == ["query", "refusal", "clarify"]
    again = questions.parse(questions.dump(cases))
    assert again == cases


def test_the_yaml_loads_as_a_pydantic_evals_dataset():
    from pydantic_evals import Dataset

    dataset = Dataset[dict, dict, dict].from_text(YAML, default_name="flights_checks")
    assert [c.name for c in dataset.cases] == ["delay_by_carrier", "runway_split", "which_airport"]


@pytest.mark.parametrize(("text", "fragment"), [
    ("cases: [", "not valid YAML"),
    ("- a", "top-level `cases:`"),
    ("cases:\n- name: a\n  inputs: {question: q}\n  expected_output: {}\n", "exactly one"),
    ("cases:\n- name: a\n  inputs: {question: q}\n  expected_output: {query: {metrics: []}}\n", "1 to 5 metric"),
    ("cases:\n- name: a\n  inputs: {question: q}\n  expected_output: {query: {metrics: [m], sql: x}}\n",
     "unknown keys: sql"),
    ("cases:\n- {name: a, inputs: {question: q}, expected_output: {clarify: true}}\n"
     "- {name: a, inputs: {question: r}, expected_output: {clarify: true}}\n", "Two cases are named a"),
])
def test_problems_are_named(text, fragment):
    with pytest.raises(EvalError) as found:
        questions.parse(text)
    assert fragment in str(found.value)


def test_questions_sql_becomes_cases():
    text = ("-- Q: Hoe ontwikkelt het verbruik zich?\n"
            '-- query_semantic_model: {"metrics": ["verbruik"], "dimensions": [{"field": "B.jaar"}]}\n'
            "SELECT 1;\n-- Q: Hoe ontwikkelt het verbruik zich?\n"
            '-- query_semantic_model: {"metrics": ["verbruik"]}\nSELECT 2;\n')
    cases = questions.from_questions_sql(text)
    assert [c.name for c in cases] == ["hoe_ontwikkelt_het_verbruik_zich", "hoe_ontwikkelt_het_verbruik_zich_2"]
    assert cases[0].expect.query == {"metrics": ["verbruik"], "dimensions": [{"field": "B.jaar"}]}
