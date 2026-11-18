"""Cron schedules in UTC, one per policy. A due schedule maintains its database unless a run is in progress."""

import asyncio
import logging
import time

from croniter import croniter

from .errors import MaintenanceError

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, services, interval=20):
        self.services = services
        self.interval = interval

    async def tick(self, now=None):
        now = now or time.time()
        store = self.services.store
        started = []
        for policy in store.due_policies(now):
            database = {"id": policy["database"], "team": policy["team"], "environment": policy["environment"]}
            store.set_next_run(policy["database"], croniter(policy["cron"], now).get_next(float))
            if store.active_run(policy["database"]):
                # Never overlap a run of the same database: skip this time.
                continue
            try:
                run = await self.services.start_run(database, "maintain", [], policy, triggered_by="schedule",
                                                    schedule=policy["cron"])
                started.append(run["id"])
            except MaintenanceError as exc:
                # A revoked environment or a database that is gone: try again next time, and say why.
                LOG.warning("The schedule of %s did not start: %s", policy["database"], exc.code)
        return started

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)
