"""Cron schedules in UTC. A due schedule starts a run unless the replication is still running."""

import asyncio
import logging
import time

from croniter import croniter

from .errors import ReplicationError

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, services, interval=20):
        self.services = services
        self.interval = interval

    async def tick(self, now=None):
        now = now or time.time()
        store = self.services.store
        started = []
        for schedule in store.due_schedules(now):
            next_run = croniter(schedule["cron"], now).get_next(float)
            replication = store.replication(schedule["replication"])
            if replication is None or store.active_run(replication["id"]):
                # Never overlap a run of the same replication: skip this time.
                store.update_schedule(schedule["replication"], next_run_at=next_run)
                continue
            try:
                run = await self.services.start_run(replication, triggered_by="schedule", schedule=schedule["cron"])
                store.update_schedule(schedule["replication"], next_run_at=next_run, last_run=run["id"])
                started.append(run["id"])
            except ReplicationError as exc:
                # A revoked environment or a missing secret: try again at the next time, and say why.
                LOG.warning("The schedule of %s did not start: %s", schedule["replication"], exc.code)
                store.update_schedule(schedule["replication"], next_run_at=next_run)
        return started

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)
