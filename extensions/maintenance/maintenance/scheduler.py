"""Cron schedules in UTC, one per database. A due schedule maintains its database unless a run is in progress."""

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
        for due in store.due_schedules(now):
            database = {"id": due["database"], "team": due["team"], "environment": due["environment"]}
            store.set_next_run(due["database"], croniter(due["cron"], now).get_next(float))
            if store.active_run(due["database"]):
                # Never overlap a run of the same database: skip this time.
                continue
            try:
                # The run resolves the policies that apply when it starts: what Polaris holds then.
                run = await self.services.start_run(database, "maintain", [], triggered_by="schedule",
                                                    schedule=due["cron"])
                started.append(run["id"])
            except MaintenanceError as exc:
                # A revoked environment or a database that is gone: try again next time, and say why.
                LOG.warning("The schedule of %s did not start: %s", due["database"], exc.code)
        return started

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)
