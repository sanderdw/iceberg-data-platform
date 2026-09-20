"""Gateway-owned SQLite definitions; immutable revisions and scoped, optimistic saves."""

import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

from server.models import ServiceError


class ReportStore:
    def __init__(self, path):
        self.path, self.connection = str(path), None
        self.lock = threading.RLock()

    def db(self):
        if self.connection is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    id TEXT PRIMARY KEY, team TEXT NOT NULL, environment TEXT NOT NULL,
                    kind TEXT NOT NULL, owner TEXT NOT NULL, revision INTEGER NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS object_scope ON objects(team, environment);
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                    author TEXT NOT NULL, saved REAL NOT NULL, PRIMARY KEY(id, revision)
                );
                PRAGMA user_version=1;
            """)
        return self.connection

    @staticmethod
    def public(row):
        return {**dict(row), "body": json.loads(row["body"])}

    def list(self, session):
        with self.lock:
            return [
                self.public(r)
                for r in self.db().execute(
                    "SELECT * FROM objects WHERE team=? AND environment=? ORDER BY updated DESC",
                    (session.team, session.environment),
                )
            ]

    def get(self, session, identifier, kind=None):
        with self.lock:
            row = (
                self.db()
                .execute(
                    "SELECT * FROM objects WHERE id=? AND team=? AND environment=?",
                    (identifier, session.team, session.environment),
                )
                .fetchone()
            )
            if row is None or kind and row["kind"] != kind:
                raise ServiceError(404, "This report or dashboard is unavailable in this workspace.")
            return self.public(row)

    @staticmethod
    def can_edit(item, session, role):
        if item["owner"] != session.user_id and role not in ("admin", "bucket-admin"):
            raise ServiceError(
                403, "Only the author or a team administrator can change this item. You can save a copy."
            )

    def save(self, session, role, kind, body, identifier=None, revision=None):
        with self.lock, self.db() as db:
            now = time.time()
            if identifier:
                old = self.get(session, identifier, kind)
                self.can_edit(old, session, role)
                if revision != old["revision"]:
                    raise ServiceError(
                        409, "Someone saved a newer version. Reload it or save your changes as a copy."
                    )
                number, owner, created = old["revision"] + 1, old["owner"], old["created"]
            else:
                if len(self.list(session)) >= 200:
                    raise ServiceError(409, "This workspace has reached its 200-item limit.")
                identifier, number, owner, created = uuid4().hex, 1, session.user_id, now
            if kind == "dashboard":
                for card in body["cards"]:
                    self.get(session, card["report_id"], "report")
            encoded = json.dumps(body, allow_nan=False)
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, session.team, session.environment, kind, owner, number, encoded, created, now),
            )
            db.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?)",
                (identifier, number, encoded, session.user_id, now),
            )
            return self.get(session, identifier)

    def delete(self, session, role, identifier, revision):
        with self.lock, self.db() as db:
            item = self.get(session, identifier)
            self.can_edit(item, session, role)
            if revision != item["revision"]:
                raise ServiceError(409, "This item changed. Reload before deleting it.")
            if item["kind"] == "report" and any(
                card["report_id"] == identifier
                for d in self.list(session)
                if d["kind"] == "dashboard"
                for card in d["body"]["cards"]
            ):
                raise ServiceError(409, "Remove this report from its dashboards before deleting it.")
            db.execute("DELETE FROM objects WHERE id=?", (identifier,))
            db.execute("DELETE FROM revisions WHERE id=?", (identifier,))

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
