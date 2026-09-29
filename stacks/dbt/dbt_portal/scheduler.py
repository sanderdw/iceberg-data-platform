"""Cron schedules in UTC. A due schedule starts a run unless the previous one is still going."""

import asyncio
import logging
import time

from croniter import croniter

from .errors import DbtError

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, services, interval=20):
        self.services = services
        self.interval = interval

    async def tick(self, now=None):
        now = now or time.time()
        started = []
        for schedule in self.services.store.due_schedules(now):
            next_run = croniter(schedule["cron"], now).get_next(float)
            previous = self.services.store.run(schedule["last_run"]) if schedule["last_run"] else None
            if previous and previous["status"] in ("queued", "running"):
                self.services.store.update_schedule(schedule["id"], next_run_at=next_run)
                continue
            try:
                run = await self.services.start_run(
                    None, schedule["project"], schedule["command"], schedule["environment"], schedule["ref"],
                    schedule["selector"], schedule=schedule["id"])
                self.services.store.update_schedule(schedule["id"], next_run_at=next_run, last_run=run["id"])
                started.append(run["id"])
            except DbtError as exc:
                # A revoked environment or a deleted branch: try again at the next time, and say why.
                LOG.warning("Schedule %s did not start: %s", schedule["id"], exc.code)
                self.services.store.update_schedule(schedule["id"], next_run_at=next_run)
        return started

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)
