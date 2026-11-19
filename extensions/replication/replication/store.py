"""This extension's own state: replications, encrypted source secrets, runs, OLake state and schedules in SQLite (WAL).

It never holds platform credentials. Teams, roles and databases are always read from the Bridge.
"""

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS replications (
  id TEXT PRIMARY KEY, team TEXT NOT NULL, environment TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', database TEXT NOT NULL, namespace TEXT NOT NULL,
  source_type TEXT NOT NULL, source TEXT NOT NULL, tables TEXT NOT NULL DEFAULT '{}',
  options TEXT NOT NULL DEFAULT '{}',
  secrets TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, created_by TEXT NOT NULL, updated_at REAL NOT NULL, updated_by TEXT NOT NULL,
  UNIQUE (team, environment, name)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, replication TEXT NOT NULL REFERENCES replications(id) ON DELETE CASCADE,
  team TEXT NOT NULL, environment TEXT NOT NULL, revision INTEGER NOT NULL, full_refresh INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'sync',
  status TEXT NOT NULL, triggered_by TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, exit_code INTEGER,
  summary TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_replication ON runs (replication, created_at DESC);
-- One replication never runs twice at once, even when two requests start it together.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs (replication) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS source_state (
  replication TEXT PRIMARY KEY REFERENCES replications(id) ON DELETE CASCADE,
  archive BLOB NOT NULL, run TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
  replication TEXT PRIMARY KEY REFERENCES replications(id) ON DELETE CASCADE,
  cron TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, next_run_at REAL NOT NULL,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, last_run TEXT NOT NULL DEFAULT ''
);
"""

JSON_FIELDS = ("source", "tables", "options", "secrets", "summary")


def new_id(prefix):
    return f"{prefix}-{secrets.token_hex(12)}"


def decode(row):
    if row:
        for key in JSON_FIELDS:
            if key in row:
                row[key] = json.loads(row[key] or "{}")
    return row


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

    def insert(self, table, fields):
        self.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                     *(json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()))

    def update(self, table, key, id, fields):
        values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        return self.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in fields)} WHERE {key} = ?",
                            *values, id)

    # Replications

    def create_replication(self, **fields):
        id, now = new_id("rp"), time.time()
        try:
            self.insert("replications", {"id": id, "created_at": now, "updated_at": now,
                                      "updated_by": fields["created_by"], **fields})
        except sqlite3.IntegrityError:
            return None
        return self.replication(id)

    def replication(self, id):
        return decode(self.one("SELECT * FROM replications WHERE id = ?", id))

    def replications(self, teams):
        if not teams:
            return []
        marks = ",".join("?" * len(teams))
        rows = self.query(f"SELECT * FROM replications WHERE team IN ({marks}) ORDER BY name, environment", *teams)
        return [decode(row) for row in rows]

    def update_replication(self, id, expected_revision=None, forget_state=False, **fields):
        """Bumps the revision; with expected_revision, only when nobody changed the replication in between.

        forget_state drops OLake's state in the same transaction, only when the update succeeds and no run
        is queued or running."""
        sets = {**fields, "updated_at": time.time()}
        values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in sets.items()]
        sql = f"UPDATE replications SET {', '.join(f'{k} = ?' for k in sets)}, revision = revision + 1 WHERE id = ?"
        args = [*values, id]
        if expected_revision is not None:
            sql, args = sql + " AND revision = ?", [*args, expected_revision]
        if forget_state:
            sql += " AND NOT EXISTS (SELECT 1 FROM runs WHERE replication = ? AND status IN ('queued', 'running'))"
            args.append(id)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                changed = self.db.execute(sql, args).rowcount
                if changed and forget_state:
                    self.db.execute("DELETE FROM source_state WHERE replication = ?", (id,))
            except sqlite3.IntegrityError:
                self.db.execute("ROLLBACK")
                return None
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")
        return self.replication(id) if changed else False

    def delete_replication(self, id):
        return self.execute("DELETE FROM replications WHERE id = ?", id)

    # Runs

    def create_run(self, **fields):
        """The new run, or None while the replication has a queued or running one."""
        id = new_id("run")
        try:
            self.insert("runs", {"id": id, "created_at": time.time(), "status": "queued", **fields})
        except sqlite3.IntegrityError:
            return None
        return self.run(id)

    def update_run(self, id, **fields):
        self.update("runs", "id", id, fields)
        return self.run(id)

    def run(self, id):
        return decode(self.one("SELECT * FROM runs WHERE id = ?", id))

    def runs(self, replication, limit=50):
        rows = self.query("SELECT * FROM runs WHERE replication = ? ORDER BY created_at DESC LIMIT ?",
                          replication, limit)
        return [decode(row) for row in rows]

    def last_runs(self, replications):
        if not replications:
            return {}
        marks = ",".join("?" * len(replications))
        rows = self.query(
            "SELECT r.* FROM runs r JOIN (SELECT replication, MAX(created_at) AS at FROM runs "
            f"WHERE kind = 'sync' AND replication IN ({marks}) GROUP BY replication) last "
            "ON last.replication = r.replication AND last.at = r.created_at AND r.kind = 'sync'", *replications)
        return {row["replication"]: decode(row) for row in rows}

    def last_check(self, replication):
        """The newest finished source check."""
        return decode(self.one("SELECT * FROM runs WHERE replication = ? AND kind = 'check' AND status NOT IN "
                               "('queued', 'running') ORDER BY created_at DESC LIMIT 1", replication))

    def unfinished_runs(self):
        return self.query("SELECT * FROM runs WHERE status IN ('queued', 'running')")

    def active_run(self, replication):
        return self.one("SELECT * FROM runs WHERE replication = ? AND status IN ('queued', 'running') LIMIT 1",
                        replication)

    def prune_runs(self, replication, keep):
        return self.query(
            "SELECT id FROM runs WHERE replication = ? AND status NOT IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?", replication, keep)

    def delete_run(self, id):
        return self.execute("DELETE FROM runs WHERE id = ?", id)

    # OLake state: the replication's state.json (change log position, unfinished first-copy chunks)

    def state(self, replication):
        row = self.one("SELECT archive FROM source_state WHERE replication = ?", replication)
        return row["archive"] if row else None

    def save_state(self, replication, archive, run):
        self.execute("INSERT OR REPLACE INTO source_state (replication, archive, run, updated_at) VALUES (?, ?, ?, ?)",
                     replication, archive, run, time.time())

    # Schedules: at most one per replication

    def schedule(self, replication):
        return self.one("SELECT * FROM schedules WHERE replication = ?", replication)

    def set_schedule(self, replication, **fields):
        self.execute("DELETE FROM schedules WHERE replication = ?", replication)
        self.insert("schedules", {"replication": replication, "created_at": time.time(), **fields})
        return self.schedule(replication)

    def update_schedule(self, replication, **fields):
        self.update("schedules", "replication", replication, fields)
        return self.schedule(replication)

    def delete_schedule(self, replication):
        return self.execute("DELETE FROM schedules WHERE replication = ?", replication)

    def due_schedules(self, now):
        return self.query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", now)
