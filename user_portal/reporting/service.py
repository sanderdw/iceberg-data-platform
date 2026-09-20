"""Viewer-scoped orchestration for the proof; no public routes are installed yet."""

import hashlib
import json
import time
from collections import OrderedDict
from copy import deepcopy

from server.models import ServiceError

from .query import ReportQuery


class ReportingProof:
    """Small bounded cache with authorization checks before and after every delivery.

    The caller serializes access and supplies a disposable-container runner. This
    proof intentionally does not provide production job scheduling or persistence.
    """

    def __init__(self, directory, runner):
        self.directory = directory
        self.runner = runner
        self.cache = OrderedDict()

    def run(self, session, database, namespace, table, query, *, refresh=False):
        started = time.monotonic()
        query = ReportQuery.model_validate(query).model_dump()
        context = (session.id, session.user_id, session.team, session.environment)
        if session.expires <= time.monotonic():
            raise ServiceError(401, "Sign in to run reports.")
        details = self.directory.details(session, database, namespace, "table", table)
        prepared = self.directory.preview_request(
            session, database, namespace, table, details["currentSnapshotId"], 100
        )
        # Preserve an empty-table preflight even if a commit arrived between the
        # two metadata requests; a subsequent refresh will see that commit.
        prepared["snapshotId"] = details["currentSnapshotId"]
        prepared.update(tableUuid=details["uuid"], schemaId=details["schemaId"])
        key = hashlib.sha256(
            json.dumps(
                [
                    context,
                    database,
                    namespace,
                    table,
                    query,
                    details["uuid"],
                    details["schemaId"],
                    details["currentSnapshotId"],
                    "UTC",
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        now = time.monotonic()
        for old_key, (until, _) in list(self.cache.items()):
            if until <= now:
                self.cache.pop(old_key)
        cached = not refresh and key in self.cache
        result = deepcopy(self.cache[key][1]) if cached else self.runner({"source": prepared, "query": query})
        if context != (session.id, session.user_id, session.team, session.environment):
            raise ServiceError(403, "Your workspace changed. Run the report again.")
        if session.expires <= time.monotonic():
            raise ServiceError(401, "Sign in to run reports.")
        # This includes a fresh directory/grants check, even on a cache hit.
        current = self.directory.details(session, database, namespace, "table", table)
        if (current["uuid"], current["schemaId"]) != (details["uuid"], details["schemaId"]):
            raise ServiceError(409, "The table or schema changed. Run the report again.")
        if not cached:
            if len(json.dumps(result).encode()) > 4_000_000:
                raise ServiceError(502, "Report result is too large.")
            self.cache[key] = (time.monotonic() + 60, deepcopy(result))
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        timing = {"querySeconds": 0, "renderSeconds": 0} if cached else result.get("timing", {})
        return {
            **result,
            "cached": cached,
            "timing": {**timing, "totalSeconds": round(time.monotonic() - started, 3)},
        }
