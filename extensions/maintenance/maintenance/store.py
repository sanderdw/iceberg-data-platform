"""This extension's own state in SQLite (WAL): policies, runs and the last known health of each table.

It never holds platform credentials. Teams, roles and databases are always read from the Bridge.
"""

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
  database TEXT PRIMARY KEY, team TEXT NOT NULL, environment TEXT NOT NULL,
  settings TEXT NOT NULL, exclude TEXT NOT NULL DEFAULT '[]',
  cron TEXT NOT NULL DEFAULT '', schedule_enabled INTEGER NOT NULL DEFAULT 0, next_run_at REAL,
  revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, created_by TEXT NOT NULL,
  updated_at REAL NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, database TEXT NOT NULL, team TEXT NOT NULL, environment TEXT NOT NULL,
  kind TEXT NOT NULL, dry_run INTEGER NOT NULL DEFAULT 0, tables TEXT NOT NULL DEFAULT '[]',
  settings TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, triggered_by TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, exit_code INTEGER,
  summary TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_database ON runs (database, created_at DESC);
-- One database never has two runs at once, even when two requests start them together.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs (database) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS tables (
  database TEXT NOT NULL, name TEXT NOT NULL, health TEXT NOT NULL DEFAULT '{}',
  inspected_at REAL NOT NULL, inspected_by TEXT NOT NULL,
  last_run TEXT NOT NULL DEFAULT '', last_status TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
  last_actions TEXT NOT NULL DEFAULT '[]', last_maintained_at REAL,
  PRIMARY KEY (database, name)
);
"""

JSON_FIELDS = ("settings", "exclude", "tables", "summary", "health", "last_actions")


def new_id(prefix):
    return f"{prefix}-{secrets.token_hex(12)}"


def decode(row):
    if row:
        for key in JSON_FIELDS:
            if key in row:
                row[key] = json.loads(row[key] or ("[]" if key in ("exclude", "tables", "last_actions") else "{}"))
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

    @staticmethod
    def encode(fields):
        return {k: json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()}

    def insert(self, table, fields):
        fields = self.encode(fields)
        self.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                     *fields.values())

    def update(self, table, key, id, fields):
        fields = self.encode(fields)
        return self.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in fields)} WHERE {key} = ?",
                            *fields.values(), id)

    # Policies: at most one per database

    def policy(self, database):
        return decode(self.one("SELECT * FROM policies WHERE database = ?", database))

    def policies(self, databases):
        if not databases:
            return {}
        marks = ",".join("?" * len(databases))
        return {row["database"]: decode(row)
                for row in self.query(f"SELECT * FROM policies WHERE database IN ({marks})", *databases)}

    def save_policy(self, database, expected_revision=None, **fields):
        """Create or change a database's policy. With expected_revision, only when nobody changed it since."""
        now = time.time()
        existing = self.policy(database)
        if existing is None:
            if expected_revision not in (None, 0):
                return False
            try:
                self.insert("policies", {"database": database, "created_at": now, "updated_at": now,
                                         "created_by": fields["updated_by"], **fields})
            except sqlite3.IntegrityError:
                return False
            return self.policy(database)
        sets = self.encode({**fields, "updated_at": now})
        sql = f"UPDATE policies SET {', '.join(f'{k} = ?' for k in sets)}, revision = revision + 1 WHERE database = ?"
        args = [*sets.values(), database]
        if expected_revision is not None:
            sql, args = sql + " AND revision = ?", [*args, expected_revision]
        changed = self.execute(sql, *args)
        return self.policy(database) if changed else False

    def delete_policy(self, database):
        return self.execute("DELETE FROM policies WHERE database = ?", database)

    def due_policies(self, now):
        return [decode(row) for row in self.query(
            "SELECT * FROM policies WHERE schedule_enabled = 1 AND cron != '' AND next_run_at <= ?", now)]

    def set_next_run(self, database, next_run_at):
        self.execute("UPDATE policies SET next_run_at = ? WHERE database = ?", next_run_at, database)

    # Runs

    def create_run(self, **fields):
        """The new run, or None while the database has a queued or running one."""
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

    def runs(self, database, limit=50):
        rows = self.query("SELECT * FROM runs WHERE database = ? ORDER BY created_at DESC LIMIT ?", database, limit)
        return [decode(row) for row in rows]

    def last_runs(self, databases):
        if not databases:
            return {}
        marks = ",".join("?" * len(databases))
        rows = self.query(
            "SELECT r.* FROM runs r JOIN (SELECT database, MAX(created_at) AS at FROM runs "
            f"WHERE database IN ({marks}) GROUP BY database) last "
            "ON last.database = r.database AND last.at = r.created_at", *databases)
        return {row["database"]: decode(row) for row in rows}

    def unfinished_runs(self):
        return self.query("SELECT * FROM runs WHERE status IN ('queued', 'running')")

    def active_run(self, database):
        return self.one("SELECT * FROM runs WHERE database = ? AND status IN ('queued', 'running') LIMIT 1", database)

    def prune_runs(self, database, keep):
        return self.query(
            "SELECT id FROM runs WHERE database = ? AND status NOT IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?", database, keep)

    def delete_run(self, id):
        return self.execute("DELETE FROM runs WHERE id = ?", id)

    # Table health: what the last run that saw a table found

    def record_tables(self, database, run, results, inspected_by, complete):
        """Store each table's health and last actions. A complete inspection also forgets dropped tables."""
        now = time.time()
        names = []
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for item in results:
                    names.append(item["name"])
                    maintained = run["kind"] == "maintain" and not run["dry_run"]
                    previous = self.db.execute("SELECT health, last_maintained_at FROM tables "
                                               "WHERE database = ? AND name = ?", (database, item["name"])).fetchone()
                    health = item.get("health") or (json.loads(previous["health"]) if previous else {})
                    self.db.execute(
                        "INSERT OR REPLACE INTO tables (database, name, health, inspected_at, inspected_by, "
                        "last_run, last_status, last_error, last_actions, last_maintained_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (database, item["name"], json.dumps(health), now, inspected_by, run["id"], item["status"],
                         item.get("error", ""), json.dumps(item.get("actions") or []),
                         now if maintained else (previous["last_maintained_at"] if previous else None)))
                if complete:
                    marks = ",".join("?" * len(names)) or "''"
                    self.db.execute(f"DELETE FROM tables WHERE database = ? AND name NOT IN ({marks})",
                                    (database, *names))
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def tables(self, databases):
        if not databases:
            return []
        marks = ",".join("?" * len(databases))
        return [decode(row) for row in self.query(
            f"SELECT * FROM tables WHERE database IN ({marks}) ORDER BY database, name", *databases)]

    def table(self, database, name):
        return decode(self.one("SELECT * FROM tables WHERE database = ? AND name = ?", database, name))
