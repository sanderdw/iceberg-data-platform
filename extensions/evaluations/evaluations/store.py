"""This extension's own state in SQLite (WAL): question sets and their versions, runs, case results and
schedules. It never holds platform credentials; teams and roles are always read from the Bridge.
Results also go to Iceberg tables in the team's database (lakehouse.py), which keep every run.
"""

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS question_sets (
  id TEXT PRIMARY KEY, team TEXT NOT NULL, environment TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL, model TEXT NOT NULL,
  rubric TEXT NOT NULL DEFAULT '', target_database TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL,
  created_at REAL NOT NULL, created_by TEXT NOT NULL, updated_at REAL NOT NULL, updated_by TEXT NOT NULL,
  UNIQUE (team, environment, name)
);
CREATE TABLE IF NOT EXISTS versions (
  question_set TEXT NOT NULL REFERENCES question_sets(id) ON DELETE CASCADE, version INTEGER NOT NULL,
  yaml TEXT NOT NULL, cases INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  created_by TEXT NOT NULL, PRIMARY KEY (question_set, version)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, question_set TEXT NOT NULL REFERENCES question_sets(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, team TEXT NOT NULL, environment TEXT NOT NULL, llm_model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, triggered_by TEXT NOT NULL, schedule TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  started_at REAL, finished_at REAL, summary TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
  published TEXT NOT NULL DEFAULT '', arm TEXT NOT NULL DEFAULT 'semantic', pair TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_set ON runs (question_set, created_at DESC);
CREATE TABLE IF NOT EXISTS case_results (
  run TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, name TEXT NOT NULL, question TEXT NOT NULL,
  kind TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', passed INTEGER NOT NULL, scores TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  queries TEXT NOT NULL DEFAULT '[]', tool_calls TEXT NOT NULL DEFAULT '[]', model TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0, trace_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run, name)
);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, question_set TEXT NOT NULL REFERENCES question_sets(id) ON DELETE CASCADE,
  cron TEXT NOT NULL, llm_model TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  next_run_at REAL NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL, last_run TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS live (
  team TEXT NOT NULL, environment TEXT NOT NULL, enabled INTEGER NOT NULL, sample_pct INTEGER NOT NULL,
  cron TEXT NOT NULL, next_run_at REAL NOT NULL, watermark REAL NOT NULL DEFAULT 0,
  target_database TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL, updated_at REAL NOT NULL,
  last_checked REAL, last_summary TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (team, environment)
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, team TEXT NOT NULL, environment TEXT NOT NULL, conversation TEXT NOT NULL,
  asked_at REAL NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, model TEXT NOT NULL, queries TEXT NOT NULL,
  llm_model TEXT NOT NULL, trace_id TEXT NOT NULL, scores TEXT NOT NULL, status TEXT NOT NULL,
  created_at REAL NOT NULL, reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at REAL, note TEXT NOT NULL DEFAULT '',
  promoted_to TEXT NOT NULL DEFAULT '', UNIQUE (team, environment, conversation)
);
CREATE INDEX IF NOT EXISTS reviews_by_team ON reviews (team, environment, status, asked_at DESC);
"""
JSON = {"model", "summary", "scores", "queries", "tool_calls", "tags", "last_summary"}


def new_id(prefix):
    return f"{prefix}-{secrets.token_hex(12)}"


def row(found):
    if found is None:
        return None
    item = dict(found)
    for key in JSON & item.keys():
        item[key] = json.loads(item[key])
    for key in ("passed", "enabled"):
        if key in item:
            item[key] = bool(item[key])
    return item


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def one(self, sql, args=()):
        with self.lock:
            return row(self.db.execute(sql, args).fetchone())

    def all(self, sql, args=()):
        with self.lock:
            return [row(r) for r in self.db.execute(sql, args).fetchall()]

    def write(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)

    # Question sets and versions

    def create_set(self, team, environment, name, agent, model, yaml, cases, by, description="", rubric="",
                   target_database=""):
        id, now = new_id("qs"), time.time()
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.execute(
                    "INSERT INTO question_sets (id, team, environment, name, description, agent, model, rubric, "
                    "target_database, version, created_at, created_by, updated_at, updated_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (id, team, environment, name, description, agent, json.dumps(model), rubric, target_database,
                     now, by, now, by))
                self.db.execute("INSERT INTO versions (question_set, version, yaml, cases, note, created_at, "
                                "created_by) VALUES (?, 1, ?, ?, 'created', ?, ?)", (id, yaml, cases, now, by))
                self.db.execute("COMMIT")
            except sqlite3.Error:
                self.db.execute("ROLLBACK")
                raise
        return self.question_set(id)

    def question_set(self, id):
        return self.one("SELECT * FROM question_sets WHERE id = ?", (id,))

    def question_sets(self, teams):
        if not teams:
            return []
        marks = ",".join("?" * len(teams))
        return self.all(f"SELECT * FROM question_sets WHERE team IN ({marks}) ORDER BY environment, name", tuple(teams))

    def update_set(self, id, by, *, yaml=None, cases=None, note="", **fields):
        """New settings and, with `yaml`, a new version."""
        allowed = {"name", "description", "model", "rubric", "target_database"}
        values = {k: json.dumps(v) if k == "model" else v for k, v in fields.items() if k in allowed}
        with self.lock:
            self.db.execute("BEGIN")
            try:
                current = self.db.execute("SELECT version FROM question_sets WHERE id = ?", (id,)).fetchone()
                version = current["version"] + (1 if yaml is not None else 0)
                if yaml is not None:
                    self.db.execute("INSERT INTO versions (question_set, version, yaml, cases, note, created_at, "
                                    "created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (id, version, yaml, cases, note, time.time(), by))
                sets = ", ".join(f"{k} = ?" for k in values)
                self.db.execute(f"UPDATE question_sets SET {sets + ', ' if sets else ''}version = ?, updated_at = ?, "
                                "updated_by = ? WHERE id = ?", (*values.values(), version, time.time(), by, id))
                self.db.execute("COMMIT")
            except sqlite3.Error:
                self.db.execute("ROLLBACK")
                raise
        return self.question_set(id)

    def delete_set(self, id):
        self.write("DELETE FROM question_sets WHERE id = ?", (id,))

    def version(self, id, version):
        return self.one("SELECT * FROM versions WHERE question_set = ? AND version = ?", (id, version))

    def versions(self, id):
        return self.all("SELECT question_set, version, cases, note, created_at, created_by FROM versions "
                        "WHERE question_set = ? ORDER BY version DESC", (id,))

    # Runs and case results

    def create_run(self, question_set, triggered_by, *, llm_model="", schedule="", arm="semantic", pair=""):
        id = new_id("run")
        with self.lock:
            self.db.execute(
                "INSERT INTO runs (id, question_set, version, team, environment, llm_model, status, triggered_by, "
                "schedule, created_at, arm, pair) SELECT ?, id, version, team, environment, ?, 'queued', ?, ?, ?, ?, ? "
                "FROM question_sets WHERE id = ?",
                (id, llm_model, triggered_by, schedule, time.time(), arm, pair, question_set))
        return self.run(id)

    def run(self, id):
        return self.one("SELECT * FROM runs WHERE id = ?", (id,))

    def runs(self, question_set, limit=50):
        return self.all("SELECT * FROM runs WHERE question_set = ? ORDER BY created_at DESC LIMIT ?",
                        (question_set, limit))

    def active_runs(self):
        return self.all("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at")

    def update_run(self, id, **fields):
        values = {k: json.dumps(v) if k in JSON else v for k, v in fields.items()}
        sets = ", ".join(f"{k} = ?" for k in values)
        self.write(f"UPDATE runs SET {sets} WHERE id = ?", (*values.values(), id))

    def add_case(self, run, case):
        values = {k: json.dumps(v) if k in JSON else int(v) if k == "passed" else v for k, v in case.items()}
        keys = ", ".join(values)
        self.write(f"INSERT OR REPLACE INTO case_results (run, {keys}) VALUES (?, {', '.join('?' * len(values))})",
                   (run, *values.values()))

    def cases(self, run):
        return self.all("SELECT * FROM case_results WHERE run = ? ORDER BY name", (run,))

    def prune(self, question_set, keep):
        """Keep the newest `keep` finished runs of a question set here; Iceberg keeps them all."""
        self.write("DELETE FROM runs WHERE question_set = ? AND status IN ('done', 'error') AND id NOT IN ("
                   "SELECT id FROM runs WHERE question_set = ? AND status IN ('done', 'error') "
                   "ORDER BY created_at DESC LIMIT ?)", (question_set, question_set, keep))

    # Schedules

    def create_schedule(self, question_set, cron, llm_model, next_run_at, by):
        id = new_id("sch")
        self.write("INSERT INTO schedules (id, question_set, cron, llm_model, next_run_at, created_by, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (id, question_set, cron, llm_model, next_run_at, by, time.time()))
        return self.schedule(id)

    def schedule(self, id):
        return self.one("SELECT * FROM schedules WHERE id = ?", (id,))

    def schedules(self, question_set):
        return self.all("SELECT * FROM schedules WHERE question_set = ? ORDER BY created_at", (question_set,))

    def due_schedules(self, now):
        return self.all("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", (now,))

    def update_schedule(self, id, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.write(f"UPDATE schedules SET {sets} WHERE id = ?", (*fields.values(), id))

    def delete_schedule(self, id):
        self.write("DELETE FROM schedules WHERE id = ?", (id,))

    # Live evaluation and its review queue

    def live(self, team, environment):
        return self.one("SELECT * FROM live WHERE team = ? AND environment = ?", (team, environment))

    def set_live(self, team, environment, enabled, sample_pct, cron, next_run_at, target_database, by):
        self.write("INSERT INTO live (team, environment, enabled, sample_pct, cron, next_run_at, target_database, "
                   "updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (team, environment) DO "
                   "UPDATE SET enabled = excluded.enabled, sample_pct = excluded.sample_pct, cron = excluded.cron, "
                   "next_run_at = excluded.next_run_at, target_database = excluded.target_database, "
                   "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                   (team, environment, int(enabled), sample_pct, cron, next_run_at, target_database, by, time.time()))
        return self.live(team, environment)

    def due_live(self, now):
        return self.all("SELECT * FROM live WHERE enabled = 1 AND next_run_at <= ?", (now,))

    def update_live(self, team, environment, **fields):
        values = {k: json.dumps(v) if k in JSON else v for k, v in fields.items()}
        sets = ", ".join(f"{k} = ?" for k in values)
        self.write(f"UPDATE live SET {sets} WHERE team = ? AND environment = ?", (*values.values(), team, environment))

    def add_review(self, team, environment, conversation, scores):
        id = new_id("rev")
        self.write("INSERT OR IGNORE INTO reviews (id, team, environment, conversation, asked_at, question, answer, "
                   "model, queries, llm_model, trace_id, scores, status, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                   (id, team, environment, conversation["id"], conversation["at"], conversation["question"],
                    conversation["answer"], json.dumps(conversation["model"]), json.dumps(conversation["queries"]),
                    conversation["llm_model"], conversation.get("trace_id") or "", json.dumps(scores), time.time()))

    def review(self, id):
        return self.one("SELECT * FROM reviews WHERE id = ?", (id,))

    def reviews(self, team, environment, status=None, limit=100):
        if status:
            return self.all("SELECT * FROM reviews WHERE team = ? AND environment = ? AND status = ? "
                            "ORDER BY asked_at DESC LIMIT ?", (team, environment, status, limit))
        return self.all("SELECT * FROM reviews WHERE team = ? AND environment = ? ORDER BY asked_at DESC LIMIT ?",
                        (team, environment, limit))

    def update_review(self, id, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.write(f"UPDATE reviews SET {sets} WHERE id = ?", (*fields.values(), id))

