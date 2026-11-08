"""Cron schedules in UTC. A due schedule starts a run unless the previous one is still going."""

import asyncio
import logging
import time

from croniter import croniter

from .errors import EvalError

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, services, interval=20, live=None):
        self.services = services
        self.interval = interval
        self.live = live

    async def tick(self, now=None):
        now = now or time.time()
        store = self.services.store
        started = []
        for schedule in store.due_schedules(now):
            next_run = croniter(schedule["cron"], now).get_next(float)
            previous = store.run(schedule["last_run"]) if schedule["last_run"] else None
            if previous and previous["status"] in ("queued", "running"):
                store.update_schedule(schedule["id"], next_run_at=next_run)
                continue
            question_set = store.question_set(schedule["question_set"])
            try:
                run = await self.services.start(question_set, f"schedule {schedule['id']}", schedule["llm_model"],
                                                schedule=schedule["id"])
                store.update_schedule(schedule["id"], next_run_at=next_run, last_run=run["id"])
                started.append(run["id"])
            except EvalError as exc:
                # A revoked environment: try again at the next time, and say why.
                LOG.warning("Schedule %s did not start: %s", schedule["id"], exc.code)
                store.update_schedule(schedule["id"], next_run_at=next_run)
        return started

    async def loop(self):
        while True:
            try:
                await self.tick()
                if self.live:
                    await self.live.tick()
            except Exception:
                LOG.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)
