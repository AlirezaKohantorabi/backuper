"""Turns the schedule settings into APScheduler cron jobs.

Sources without their own schedule run together at the main schedule (so
their uploads are size-ordered as one batch). A source with its own schedule
runs at those times instead. Sources sharing a cron line share one run.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backuper.db import Store
from backuper.engine import Engine
from backuper.fields import describe_schedule

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, engine: Engine, store: Store, tz):
        self.engine = engine
        self.store = store
        self.tz = tz
        self._sched = AsyncIOScheduler(timezone=tz, job_defaults={
            "coalesce": True, "max_instances": 1, "misfire_grace_time": 3600,
        })

    def start(self) -> None:
        self.reload()
        self._sched.start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)

    def _plan(self) -> dict[str, list[int]]:
        """cron line -> ids of enabled sources that run at it."""
        main = [c for c in (self.store.get_setting("schedule") or "").split(";") if c]
        plan: dict[str, list[int]] = {}
        for src in self.store.list_sources(enabled_only=True):
            own = [c for c in (src.config.get("schedule") or "").split(";") if c]
            for cron in own or main:
                plan.setdefault(cron, []).append(src.id)
        return plan

    def reload(self) -> None:
        self._sched.remove_all_jobs()
        for cron in self._plan():
            try:
                trigger = CronTrigger.from_crontab(cron, timezone=self.tz)
            except ValueError:
                log.error("Ignoring invalid cron %r", cron)
                continue
            self._sched.add_job(self._fire, trigger, args=[cron], id=f"cron:{cron}", replace_existing=True)
        log.info("Schedule loaded: %s", self.describe() or "nothing scheduled")

    async def _fire(self, cron: str) -> None:
        ids = self._plan().get(cron, [])
        if not ids:
            return
        label = "scheduled" if cron in (self.store.get_setting("schedule") or "").split(";") else \
            f"scheduled ({describe_schedule(cron)})"
        await self.engine.run(ids, trigger=label)

    def describe(self) -> str:
        lines = []
        for when, desc, n in self.next_runs():
            nxt = when.strftime("%Y-%m-%d %H:%M") if when else "—"
            lines.append(f"{desc} → next {nxt} ({n} sources)")
        return "\n".join(lines)

    def next_runs(self) -> list[tuple[datetime, str, int]]:
        plan = self._plan()
        out = []
        for job in self._sched.get_jobs():
            cron = job.id.split(":", 1)[1]
            # pending jobs (scheduler not started yet) have no next_run_time attribute
            when = getattr(job, "next_run_time", None) or job.trigger.get_next_fire_time(None, datetime.now(self.tz))
            out.append((when, describe_schedule(cron), len(plan.get(cron, []))))
        far = datetime.max.replace(tzinfo=self.tz)
        return sorted(out, key=lambda x: x[0] or far)
