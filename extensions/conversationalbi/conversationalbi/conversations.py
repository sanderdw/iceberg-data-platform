"""The conversation record, kept only for teams that asked for it.

A chat run is recorded while a team administrator has live evaluation switched on, in the
Evaluations extension, for the team and environment of the semantic model the agent answered from.
Evaluations tells Conversational BI when that starts and stops (evaluation.py); stopping deletes
what was kept. Everything else stays in memory, as before. A record holds the question, the answer,
the tool calls with what the agent saw of their results, the LLM, token usage and the trace id,
and not who asked. It is kept BI_CONVERSATION_RETENTION_DAYS days (30; 0 records nothing) and is
readable only by the Evaluations extension, for that team.
"""

import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart

from .services import milliseconds, timestamp

LOG = logging.getLogger(__name__)
# `user` is no longer written (it holds ""): a record is about the agent's answer, not about who asked.
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, at REAL NOT NULL, team TEXT NOT NULL, environment TEXT NOT NULL, user TEXT NOT NULL,
  model TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, tool_calls TEXT NOT NULL,
  queries TEXT NOT NULL, llm_model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  trace_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_by_team ON conversations (team, environment, at);
CREATE TABLE IF NOT EXISTS recording (
  team TEXT NOT NULL, environment TEXT NOT NULL, since REAL NOT NULL, PRIMARY KEY (team, environment)
);
"""
MAX_TEXT = 20_000
MAX_JSON = 200_000
# A time in whole milliseconds, as responses show it and as `since` is compared: milliseconds() in SQL.
AT_MS = "CAST(at * 1000 + 0.5 AS INTEGER)"


def public_conversation(row):
    """A stored conversation as the evaluation API returns it."""
    return {"id": row["id"], "at": timestamp(row["at"]), "team": row["team"], "environment": row["environment"],
            "model": json.loads(row["model"]), "question": row["question"], "answer": row["answer"],
            "toolCalls": json.loads(row["tool_calls"]), "queries": json.loads(row["queries"]),
            "llmModel": row["llm_model"], "inputTokens": row["input_tokens"], "outputTokens": row["output_tokens"],
            "traceId": row["trace_id"] or None}


def bounded(items):
    """The list as JSON within MAX_JSON characters. A longer one keeps its entries without their results,
    or is left empty: cutting the text would leave JSON that no longer reads back."""
    text = json.dumps(items)
    if len(text) > MAX_JSON:
        text = json.dumps([{k: v for k, v in item.items() if k not in ("result", "rows")} for item in items])
    return text if len(text) <= MAX_JSON else "[]"


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

    def recording(self, team, environment):
        """Whether this team has live evaluation switched on there."""
        if not self.enabled:
            return False
        with self.lock:
            return self.db.execute("SELECT 1 FROM recording WHERE team = ? AND environment = ?",
                                   (team, environment)).fetchone() is not None

    def set_recording(self, team, environment, on):
        """Start keeping this team's conversations there, or stop and delete the ones that were kept."""
        if not self.enabled:
            return
        with self.lock:
            if on:
                self.db.execute("INSERT OR IGNORE INTO recording (team, environment, since) VALUES (?, ?, ?)",
                                (team, environment, time.time()))
            else:
                self.db.execute("DELETE FROM recording WHERE team = ? AND environment = ?", (team, environment))
                self.db.execute("DELETE FROM conversations WHERE team = ? AND environment = ?",
                                (team, environment))

    def add(self, entry):
        with self.lock:
            # `at` is the cursor of `since`: within a team environment no two conversations share a millisecond.
            latest = self.db.execute("SELECT max(at) FROM conversations WHERE team = ? AND environment = ?",
                                     (entry["team"], entry["environment"])).fetchone()[0]
            at = time.time()
            if latest is not None and milliseconds(at) <= milliseconds(latest):
                at = (milliseconds(latest) + 1) / 1000
            self.db.execute(
                "INSERT INTO conversations (id, at, team, environment, user, model, question, answer, tool_calls, "
                "queries, llm_model, input_tokens, output_tokens, trace_id) "
                "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("conv-" + secrets.token_hex(12), at, entry["team"], entry["environment"],
                 json.dumps(entry["model"]), entry["question"][:MAX_TEXT], entry["answer"][:MAX_TEXT],
                 bounded(entry["toolCalls"]), bounded(entry["queries"]),
                 entry["llmModel"], entry["inputTokens"], entry["outputTokens"], entry["traceId"] or ""))
        self.prune()

    def prune(self, now=None):
        now = now or time.time()
        if now - self.pruned < 3600:
            return
        self.pruned = now
        with self.lock:
            self.db.execute("DELETE FROM conversations WHERE at < ?", (now - self.retention,))

    def since(self, team, environment, after, limit):
        """The team's conversations there, oldest first; `after` is a time in milliseconds since 1970, or None."""
        if not self.enabled:
            return []
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM conversations WHERE team = ? AND environment = ? AND (? IS NULL OR {AT_MS} > ?) "
                "ORDER BY at LIMIT ?", (team, environment, after, after, limit)).fetchall()
        return [public_conversation(row) for row in rows]


class Recorder:
    """Records a finished chat run while its team has live evaluation on; never lets the chat fail over it."""

    def __init__(self, log, services):
        self.log = log
        self.services = services

    async def record(self, caller, me, state, result):
        # A run that ends waiting for the browser (a chart) has no answer yet; the next run of the turn has.
        if state.model is None or not isinstance(result.output, str) or not self.log.enabled:
            return
        from .evaluation import CAPABILITY, queries, trace_id, transcript

        try:
            access = await self.services.resolve(caller, state.model, me)
            team, environment = access["readingTeam"], access["database"]["environment"]
            if not self.log.recording(team, environment):
                return
            bridge = self.services.bridge
            # Evaluations was disabled for the team without switching live evaluation off first.
            if (CAPABILITY not in await bridge.capabilities()
                    or "evaluations" not in await bridge.team_extensions(team, environment)):
                self.log.set_recording(team, environment, False)
                return
            question, messages = turn(result.all_messages())
            calls = transcript(messages)
            usage = result.usage
            self.log.add({"team": team, "environment": environment,
                          "model": state.model.model_dump(), "question": question, "answer": result.output,
                          "toolCalls": calls, "queries": queries(calls),
                          "llmModel": result.response.model_name or self.services.settings.model,
                          "inputTokens": usage.input_tokens,
                          "outputTokens": usage.output_tokens, "traceId": trace_id()})
        except Exception as exc:  # noqa: BLE001 - recording must never break the chat
            LOG.warning("Conversation not recorded: %s", type(exc).__name__)
