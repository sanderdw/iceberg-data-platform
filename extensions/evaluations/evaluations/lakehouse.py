"""Results as Iceberg tables in the team's own database, next to its data.

After a run, this extension writes it as its automation principal for the question set's team and
environment (a write token from the Bridge) to the question set's target database:

- `evaluations.runs`: one row per run.
- `evaluations.case_results`: one row per case per run, with a column per scorer (null when a
  scorer did not apply).

Both are append-only, so they keep every run; this extension's own store keeps only the newest.
Next to them it publishes the semantic model `evaluations.evaluation_results`, so people and
agents ask "is the agent getting better?" through Conversational BI or the user portal, and
the tables are readable with DuckDB in notebooks like any other.
"""

import asyncio
import datetime
import json
from urllib.parse import quote

import httpx
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.io.pyarrow import PyArrowFileIO

from .config import VERSION
from .errors import EvalError

NAMESPACE = "evaluations"
MODEL = "evaluation_results"
PREFIX = "iceberg-data-platform."
SCORERS = ("result_matches", "right_metrics", "grounded", "refused", "clarified", "declined", "judge")
ENDPOINT = "iceberg-platform.storage-endpoint"
UTC = pa.timestamp("us", tz="UTC")

RUNS = pa.schema([
    ("run_id", pa.string()), ("question_set", pa.string()), ("question_set_name", pa.string()),
    ("arm", pa.string()), ("version", pa.int32()), ("team", pa.string()), ("environment", pa.string()),
    ("agent", pa.string()),
    ("llm_model", pa.string()), ("models", pa.string()), ("triggered_by", pa.string()),
    ("started_at", UTC), ("finished_at", UTC), ("cases", pa.int32()), ("passed", pa.int32()),
    ("pass_rate", pa.float64()), ("errors", pa.int32()), ("input_tokens", pa.int64()), ("output_tokens", pa.int64()),
    ("median_seconds", pa.float64()), ("p95_seconds", pa.float64()),
])
CASES = pa.schema([
    ("run_id", pa.string()), ("question_set_name", pa.string()), ("arm", pa.string()), ("version", pa.int32()),
    ("llm_model", pa.string()), ("triggered_by", pa.string()), ("finished_at", UTC), ("case_name", pa.string()),
    ("question", pa.string()), ("kind", pa.string()), ("tags", pa.string()), ("passed", pa.bool_()),
    *[(name, pa.bool_()) for name in SCORERS], ("reasons", pa.string()), ("duration_seconds", pa.float64()),
    ("input_tokens", pa.int64()), ("output_tokens", pa.int64()), ("trace_id", pa.string()), ("error", pa.string()),
])
LIVE = pa.schema([
    ("conversation_id", pa.string()), ("team", pa.string()), ("environment", pa.string()), ("asked_at", UTC),
    ("checked_at", UTC), ("llm_model", pa.string()), ("question", pa.string()), ("passed", pa.bool_()),
    ("grounded", pa.bool_()), ("refusal_explained", pa.bool_()), ("judge", pa.bool_()), ("reasons", pa.string()),
    ("trace_id", pa.string()),
])
COMMENTS = {
    "live_scores": "Live evaluation: real Conversational BI conversations of this team, scored by the Evaluations "
                   "extension. One row per sampled conversation; the question, never the answer's data.",
    "runs": "Evaluation runs of the Evaluations extension: one row per run of a question set.",
    "case_results": "Evaluation results: one row per question per run, with a column per scorer (null when it "
                    "did not apply). The Evaluations extension appends a run when it finishes.",
}


class InternalS3FileIO(PyArrowFileIO):
    """Polaris vends the browser-facing storage address; this service reaches the same buckets inside."""

    def __init__(self, properties=None):
        properties = dict(properties or {})
        if properties.get(ENDPOINT):
            properties["s3.endpoint"] = properties[ENDPOINT]
        super().__init__(properties)


def at(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.UTC) if seconds else None


def run_row(run, question_set):
    s = run.get("summary") or {}
    return {
        "run_id": run["id"], "question_set": question_set["id"], "question_set_name": question_set["name"],
        "arm": run.get("arm", "semantic"), "version": run["version"], "team": run["team"],
        "environment": run["environment"],
        "agent": question_set["agent"], "llm_model": run["llm_model"] or "default",
        "models": ",".join(s.get("models", [])), "triggered_by": run["triggered_by"],
        "started_at": at(run.get("started_at")), "finished_at": at(run.get("finished_at")),
        "cases": s.get("cases", 0), "passed": s.get("passed", 0), "pass_rate": s.get("passRate", 0.0),
        "errors": s.get("errors", 0),
        "input_tokens": s.get("inputTokens", 0), "output_tokens": s.get("outputTokens", 0),
        "median_seconds": s.get("medianSeconds"), "p95_seconds": s.get("p95Seconds"),
    }


def case_rows(run, question_set, cases):
    rows = []
    # A question without an answer is no measurement: pass_rate counts answered questions, as the run does.
    for case in (c for c in cases if not c.get("error")):
        scores = case.get("scores") or {}
        rows.append({
            "run_id": run["id"], "question_set_name": question_set["name"], "arm": run.get("arm", "semantic"),
            "version": run["version"],
            "llm_model": case.get("model") or run["llm_model"] or "default", "triggered_by": run["triggered_by"],
            "finished_at": at(run.get("finished_at")), "case_name": case["name"], "question": case["question"],
            "kind": case["kind"], "tags": ",".join(case.get("tags") or []), "passed": bool(case["passed"]),
            **{name: (scores[name]["value"] if name in scores else None) for name in SCORERS},
            "reasons": json.dumps({k: v.get("reason", "") for k, v in scores.items()}),
            "duration_seconds": (case.get("duration_ms") or 0) / 1000, "input_tokens": case.get("input_tokens", 0),
            "output_tokens": case.get("output_tokens", 0), "trace_id": case.get("trace_id", ""),
            "error": case.get("error", ""),
        })
    return rows


def field(name, description=""):
    return {"name": name, **({"description": description} if description else {}),
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]}}


def metric(name, expression, description):
    return {"name": name, "description": description,
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}}


def semantic_model():
    """An Apache Ossie model over case_results: pass rates and costs by LLM, question set, scorer and time."""
    rate = "100.0 * count(*) FILTER (WHERE CASE_RESULT.{0}) / nullif(count(CASE_RESULT.{0}), 0)"
    return {
        "name": "Evaluation results",
        "description": "How well the platform's AI agent answers the question sets of this team: one row in "
                       "CASE_RESULT per question per evaluation run.",
        "ai_context": {"instructions": (
            "Time: finished_at is the UTC time the run finished; use a week or day grain for trends.\n"
            "Grain: one row per question per run. A run is run_id; compare LLMs with llm_model.\n"
            "Missing values: a scorer column is NULL when that check did not apply to the question; the "
            "per-scorer rates count only questions it applied to.\n"
            "Arm: arm is semantic for the agent with its semantic model and control for the same LLM with only the "
            "table schemas. Filter on arm = semantic for how the agent is doing; split by arm for what the model "
            "adds.\n"
            "Owner: the Evaluations extension writes this table after every run.")},
        "datasets": [{
            "name": "CASE_RESULT", "source": f"lakehouse.{NAMESPACE}.case_results",
            "description": COMMENTS["case_results"], "primary_key": ["run_id", "case_name"],
            "fields": [field("run_id", "The evaluation run."), field("question_set_name", "The question set."),
                       field("arm", "semantic: with the semantic model. control: the same LLM with only the schemas."),
                       field("version", "The question set's version the run used."),
                       field("llm_model", "The LLM that answered."), field("triggered_by", "Who or which schedule "
                                                                                           "started the run."),
                       field("finished_at", "When the run finished (UTC)."), field("case_name", "The question's name."),
                       field("question"), field("kind", "What a good answer is: query, refusal, clarify or decline."),
                       field("tags"), field("passed", "Every check that applied passed."),
                       *[field(name) for name in SCORERS], field("duration_seconds"), field("input_tokens"),
                       field("output_tokens"), field("trace_id", "The OpenTelemetry trace of the answer.")],
        }],
        "relationships": [],
        "metrics": [
            metric("pass_rate", "100.0 * count(*) FILTER (WHERE CASE_RESULT.passed) / nullif(count(*), 0)",
                   "Share of questions answered well: every check that applied passed. Unit: percent."),
            metric("questions", "count(*)", "Questions asked."),
            metric("failed_questions", "count(*) FILTER (WHERE NOT CASE_RESULT.passed)", "Questions answered badly."),
            metric("grounded_rate", rate.format("grounded"),
                   "Share of answers whose every figure came from a query result. Unit: percent."),
            metric("result_match_rate", rate.format("result_matches"),
                   "Share of answers with the expected query result. Unit: percent."),
            metric("average_seconds", "avg(CASE_RESULT.duration_seconds)", "Time to answer. Unit: seconds."),
            metric("average_tokens", "avg(CASE_RESULT.input_tokens + CASE_RESULT.output_tokens)",
                   "LLM tokens per answer, input and output together. Unit: tokens."),
        ],
    }


def append(catalog, name, schema, rows, properties):
    table = catalog.create_table_if_not_exists((NAMESPACE, name), schema=schema)
    # Columns added in a later version join the existing table.
    with table.update_schema() as update:
        update.union_by_name(schema)
    table = catalog.load_table((NAMESPACE, name))
    table.append(pa.Table.from_pylist(rows, schema=schema))
    with table.transaction() as transaction:
        transaction.set_properties({"comment": COMMENTS[name], **properties})


def publish_model(uri, warehouse, token, http=None):
    """'created', 'updated', 'unchanged' or 'off' (semantic models switched off in Polaris)."""
    http = http or httpx.Client(timeout=30, trust_env=False)
    url = f"{uri}/polaris/v1/{quote(warehouse, safe='')}/namespaces/{NAMESPACE}/semantic-models"
    headers = {"Authorization": f"Bearer {token}"}
    document = {"version": "0.2.0", "semantic_model": json.dumps({"version": "0.2.0",
                                                                   "semantic_model": [semantic_model()]})}
    current = http.get(f"{url}/{MODEL}", headers=headers)
    if current.status_code == 404:
        http.post(url, json={"name": MODEL, "document": document}, headers=headers).raise_for_status()
        return "created"
    if current.status_code == 406:
        return "off"
    current.raise_for_status()
    found = current.json()
    if found.get("document", {}).get("semantic_model") == document["semantic_model"]:
        return "unchanged"
    http.put(f"{url}/{MODEL}", json={"document": document, "entity-version": found.get("entity-version")},
             headers=headers).raise_for_status()
    return "updated"


class Lakehouse:
    def __init__(self, settings, bridge, catalog=None, http=None):
        self.settings = settings
        self.bridge = bridge
        # Injected in tests: a local PyIceberg catalog instead of Polaris.
        self.catalog = catalog
        self.http = http

    async def target(self, team, environment, database_id, purpose):
        """The database, a write token and discovery, or why results can't go there."""
        principal = await self.bridge.principal_for(team, environment)
        scope = await self.bridge.scope(principal["id"])
        database = next((d for d in scope.get("databases", []) if d["id"] == database_id), None)
        if database is None:
            raise EvalError(409, "The target database is not a database of this team in this environment.",
                            "unknown_database")
        token = await self.bridge.catalog_token(principal["id"], "write", purpose)
        discovery = await self.bridge.discovery()
        return database, token, discovery

    def open(self, database, token, discovery):
        return self.catalog or load_catalog(
            f"evaluations-{database['id']}", type="rest", uri=token["catalog"]["internalUri"], warehouse=database["id"],
            token=token["accessToken"], **{"header.X-Iceberg-Access-Delegation": "vended-credentials",
                                           "py-io-impl": "evaluations.lakehouse.InternalS3FileIO",
                                           ENDPOINT: discovery["storage"]["internalEndpoint"]})

    async def publish_live(self, team, environment, database_id, rows):
        database, token, discovery = await self.target(team, environment, database_id, "live evaluation scores")
        now = datetime.datetime.now(datetime.UTC)
        records = [{"conversation_id": r["id"], "team": team, "environment": environment, "asked_at": at(r["at"]),
                    "checked_at": now, "llm_model": r.get("llm_model", ""), "question": r["question"],
                    "passed": r["passed"], **{k: r["scores"].get(k, {}).get("value") for k in
                                              ("grounded", "refusal_explained", "judge")},
                    "reasons": json.dumps({k: v["reason"] for k, v in r["scores"].items()}),
                    "trace_id": r.get("trace_id") or ""} for r in rows]
        properties = {PREFIX + "producer": "evaluations", PREFIX + "producer.version": f"evaluations {VERSION}",
                      PREFIX + "producer.run-at": now.isoformat(timespec="seconds")}

        def write():
            catalog = self.open(database, token, discovery)
            catalog.create_namespace_if_not_exists((NAMESPACE,))
            append(catalog, "live_scores", LIVE, records, properties)

        try:
            await asyncio.to_thread(write)
        except Exception as exc:
            raise EvalError(502, f"The catalog refused the scores ({type(exc).__name__}).", "publish_failed") from exc
        return f"{database['name']}.{NAMESPACE}.live_scores"

    async def publish(self, run, question_set, cases):
        if not question_set.get("target_database"):
            return ""
        summary = run.get("summary") or {}
        if not summary.get("reliable", False):
            # Kept out of the team's trend: an outage would read as the agent getting worse.
            return (f"not published: {summary.get('errors', 0)} of {summary.get('cases', 0)} questions got no answer, "
                    "so this run measures nothing")
        database, token, discovery = await self.target(run["team"], run["environment"],
                                                       question_set["target_database"],
                                                       f"evaluation results {run['id']}")
        finished = at(run.get("finished_at")) or datetime.datetime.now(datetime.UTC)
        properties = {PREFIX + "producer": "evaluations", PREFIX + "producer.version": f"evaluations {VERSION}",
                      PREFIX + "producer.run-id": run["id"],
                      PREFIX + "producer.run-at": finished.isoformat(timespec="seconds"),
                      PREFIX + "producer.url": f"{self.settings.origin}/#/runs/{run['id']}"}
        uri = token["catalog"]["internalUri"]

        def write():
            catalog = self.open(database, token, discovery)
            catalog.create_namespace_if_not_exists((NAMESPACE,))
            append(catalog, "runs", RUNS, [run_row(run, question_set)], properties)
            append(catalog, "case_results", CASES, case_rows(run, question_set, cases), properties)
            return publish_model(uri, database["id"], token["accessToken"], self.http)

        try:
            model = await asyncio.to_thread(write)
        except Exception as exc:
            raise EvalError(502, f"The catalog refused the results ({type(exc).__name__}).", "publish_failed") from exc
        suffix = "" if model in ("created", "updated", "unchanged") else f"; semantic model {model}"
        return f"{database['name']}.{NAMESPACE}.case_results{suffix}"
