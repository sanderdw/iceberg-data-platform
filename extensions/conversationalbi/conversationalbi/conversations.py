"""The conversation record, kept only for teams that asked for it.

A chat run is recorded when a team administrator enabled the Evaluations extension for the team
and environment of the semantic model the agent answered from: that is how a team opts in to live
evaluation. Everything else stays in memory, as before. A record holds the question, the answer,
the tool calls with what the agent saw of their results, the LLM, token usage and the trace id. It
is kept BI_CONVERSATION_RETENTION_DAYS days (30; 0 records nothing) and is readable only by the
Evaluations extension, for that team (evaluation.py).
"""

import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart

LOG = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, at REAL NOT NULL, team TEXT NOT NULL, environment TEXT NOT NULL, user TEXT NOT NULL,
  model TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, tool_calls TEXT NOT NULL,
  queries TEXT NOT NULL, llm_model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  trace_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_by_team ON conversations (team, environment, at);
"""
MAX_TEXT = 20_000


def turn(messages):
    """The last question and everything after it: a chart in the browser splits one turn into two runs."""
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content, messages[i:]
    return "", messages


class ConversationLog:
    """Opened on first use; without a writable state directory it records nothing and says so once."""

    def __init__(self, path, retention_days):
        self.path = Path(path)
        self.retention = retention_days * 86400
        self.db = None
        self.failed = retention_days <= 0
        self.lock = threading.Lock()
        self.pruned = 0.0

    @property
    def enabled(self):
        if self.db is None and not self.failed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)
                self.db = db
            except (OSError, sqlite3.Error) as exc:
                self.failed = True
                LOG.error("No conversation record for live evaluation: %s is not writable (%s). Mount a volume "
                          "there.", self.path.parent, type(exc).__name__)
        return self.db is not None

    def add(self, entry):
        with self.lock:
            self.db.execute(
                "INSERT INTO conversations (id, at, team, environment, user, model, question, answer, tool_calls, "
                "queries, llm_model, input_tokens, output_tokens, trace_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("conv-" + secrets.token_hex(12), time.time(), entry["team"], entry["environment"], entry["user"],
                 json.dumps(entry["model"]), entry["question"][:MAX_TEXT], entry["answer"][:MAX_TEXT],
                 json.dumps(entry["toolCalls"])[:200_000], json.dumps(entry["queries"])[:200_000],
                 entry["llmModel"], entry["inputTokens"], entry["outputTokens"], entry["traceId"] or ""))
        self.prune()

    def prune(self, now=None):
        now = now or time.time()
        if now - self.pruned < 3600:
            return
        self.pruned = now
        with self.lock:
            self.db.execute("DELETE FROM conversations WHERE at < ?", (now - self.retention,))

    def since(self, team, environment, since, limit):
        if not self.enabled:
            return []
        with self.lock:
            rows = self.db.execute("SELECT * FROM conversations WHERE team = ? AND environment = ? AND at > ? "
                                   "ORDER BY at LIMIT ?", (team, environment, since, limit)).fetchall()
        found = []
        for row in rows:
            item = dict(row)
            for key in ("model", "tool_calls", "queries"):
                item[key] = json.loads(item[key])
            found.append(item)
        return found


class Recorder:
    """Records a finished chat run when its team opted in; never lets the chat fail over it."""

    def __init__(self, log, services):
        self.log = log
        self.services = services

    async def record(self, caller, me, state, result):
        # A run that ends waiting for the browser (a chart) has no answer yet; the next run of the turn has.
        if state.model is None or not isinstance(result.output, str) or not self.log.enabled:
            return
        from .evaluation import CAPABILITY, queries, trace_id, transcript

        try:
            bridge = self.services.bridge
            if CAPABILITY not in await bridge.capabilities():
                return
            access = await self.services.resolve(caller, state.model, me)
            team, environment = access["readingTeam"], access["database"]["environment"]
            if "evaluations" not in await bridge.team_extensions(team, environment):
                return
            question, messages = turn(result.all_messages())
            calls = transcript(messages)
            usage = result.usage
            self.log.add({"team": team, "environment": environment, "user": me["user"]["id"],
                          "model": state.model.model_dump(), "question": question, "answer": result.output,
                          "toolCalls": calls, "queries": queries(calls),
                          "llmModel": result.response.model_name or "", "inputTokens": usage.input_tokens,
                          "outputTokens": usage.output_tokens, "traceId": trace_id()})
        except Exception as exc:  # noqa: BLE001 - recording must never break the chat
            LOG.warning("Conversation not recorded: %s", type(exc).__name__)
