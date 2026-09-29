"""This stack's own state: projects, runs, node results and schedules in SQLite (WAL).

It never holds platform credentials. Teams, roles and databases are always read from the Bridge.
"""

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, team TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  default_database TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (team, name)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  environment TEXT NOT NULL, command TEXT NOT NULL, selector TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL, revision TEXT NOT NULL, access TEXT NOT NULL, status TEXT NOT NULL,
  triggered_by TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, exit_code INTEGER,
  summary TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_project ON runs (project, environment, created_at DESC);
CREATE TABLE IF NOT EXISTS node_results (
  run TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, project TEXT NOT NULL, environment TEXT NOT NULL,
  node TEXT NOT NULL, status TEXT NOT NULL, execution_time REAL, message TEXT NOT NULL DEFAULT '',
  rows_affected INTEGER, finished_at REAL NOT NULL, PRIMARY KEY (run, node)
);
CREATE INDEX IF NOT EXISTS node_results_latest ON node_results (project, environment, node, finished_at DESC);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  environment TEXT NOT NULL, cron TEXT NOT NULL, command TEXT NOT NULL, selector TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, next_run_at REAL NOT NULL,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, last_run TEXT NOT NULL DEFAULT ''
);
"""


def new_id(prefix):
    return f"{prefix}-{secrets.token_hex(12)}"


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

    def query(self, sql, *args):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def one(self, sql, *args):
        rows = self.query(sql, *args)
        return rows[0] if rows else None

    def execute(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).rowcount

    # Projects

    def create_project(self, team, name, description, default_database, created_by):
        id = new_id("prj")
        try:
            self.execute(
                "INSERT INTO projects (id, team, name, description, default_database, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", id, team, name, description, default_database, time.time(), created_by)
        except sqlite3.IntegrityError:
            return None
        return self.project(id)

    def project(self, id):
        return self.one("SELECT * FROM projects WHERE id = ?", id)

    def projects(self, teams):
        if not teams:
            return []
        marks = ",".join("?" * len(teams))
        return self.query(f"SELECT * FROM projects WHERE team IN ({marks}) ORDER BY name", *teams)

    def delete_project(self, id):
        return self.execute("DELETE FROM projects WHERE id = ?", id)

    # Runs

    def create_run(self, **fields):
        id = new_id("run")
        fields = {"id": id, "created_at": time.time(), "status": "queued", **fields}
        self.execute(f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                     *fields.values())
        return self.run(id)

    def update_run(self, id, **fields):
        if "summary" in fields and not isinstance(fields["summary"], str):
            fields["summary"] = json.dumps(fields["summary"])
        self.execute(f"UPDATE runs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), id)
        return self.run(id)

    def run(self, id):
        row = self.one("SELECT * FROM runs WHERE id = ?", id)
        if row:
            row["summary"] = json.loads(row["summary"] or "{}")
        return row

    def runs(self, project, environment=None, limit=50):
        sql, args = "SELECT * FROM runs WHERE project = ?", [project]
        if environment:
            sql, args = sql + " AND environment = ?", [*args, environment]
        rows = self.query(sql + " ORDER BY created_at DESC LIMIT ?", *args, limit)
        for row in rows:
            row["summary"] = json.loads(row["summary"] or "{}")
        return rows

    def unfinished_runs(self):
        return self.query("SELECT * FROM runs WHERE status IN ('queued', 'running')")

    def record_nodes(self, run, project, environment, results, finished_at):
        with self.lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO node_results (run, project, environment, node, status, execution_time, "
                "message, rows_affected, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run, project, environment, r["node"], r["status"], r.get("executionTime"),
                  (r.get("message") or "")[:500], r.get("rowsAffected"), finished_at) for r in results],
            )

    def latest_nodes(self, project, environment):
        """The most recent result of each node in an environment, across runs."""
        return self.query(
            "SELECT n.* FROM node_results n JOIN (SELECT node, MAX(finished_at) AS at FROM node_results "
            "WHERE project = ? AND environment = ? GROUP BY node) latest ON latest.node = n.node "
            "AND latest.at = n.finished_at WHERE n.project = ? AND n.environment = ?",
            project, environment, project, environment)

    # Schedules

    def create_schedule(self, **fields):
        id = new_id("sch")
        fields = {"id": id, "created_at": time.time(), **fields}
        self.execute(f"INSERT INTO schedules ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                     *fields.values())
        return self.schedule(id)

    def schedule(self, id):
        return self.one("SELECT * FROM schedules WHERE id = ?", id)

    def schedules(self, project):
        return self.query("SELECT * FROM schedules WHERE project = ? ORDER BY environment, cron", project)

    def update_schedule(self, id, **fields):
        self.execute(f"UPDATE schedules SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     *fields.values(), id)
        return self.schedule(id)

    def delete_schedule(self, id):
        return self.execute("DELETE FROM schedules WHERE id = ?", id)

    def due_schedules(self, now):
        return self.query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", now)
