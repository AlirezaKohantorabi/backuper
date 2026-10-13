"""Turns groups and per-source times into APScheduler cron jobs.

* A group runs all its (enabled) sources together at the group's time(s); the
  run uploads their zips smallest first, one after another.
* A source outside a group runs by itself at its own time(s), if it has one.
* Everything else is manual-only (▶️ Run now).
Uploads of overlapping runs never happen at the same time (see Engine).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backuper.db import Store
from backuper.engine import Engine
from backuper.fields import describe_schedule

log = logging.getLogger(__name__)


@dataclass
class PlannedJob:
    key: str              # "group:<id>:<cron>" or "source:<id>:<cron>"
    cron: str
    label: str            # "group Daily" / source name
    source_ids: list[int]
    upload_at: str = ""   # groups only: "HH:MM" uploads don't start before


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

    def plan(self) -> dict[str, PlannedJob]:
        groups = {g.name.lower(): g for g in self.store.list_groups()}
        members: dict[int, list[int]] = defaultdict(list)
        jobs: dict[str, PlannedJob] = {}
        for src in self.store.list_sources(enabled_only=True):
            gname = (src.config.get("group") or "").strip().lower()
            group = groups.get(gname) if gname else None
            if group is not None:
                members[group.id].append(src.id)
                continue
            for cron in [c for c in (src.config.get("schedule") or "").split(";") if c]:
                jobs[f"source:{src.id}:{cron}"] = PlannedJob(f"source:{src.id}:{cron}", cron, src.name, [src.id])
        for g in groups.values():
            if not g.enabled or not members[g.id]:
                continue
            for cron in [c for c in (g.schedule or "").split(";") if c]:
                key = f"group:{g.id}:{cron}"
                jobs[key] = PlannedJob(key, cron, f"group {g.name}", members[g.id], g.upload_at)
        return jobs

    def reload(self) -> None:
        self._sched.remove_all_jobs()
        for job in self.plan().values():
            try:
                trigger = CronTrigger.from_crontab(job.cron, timezone=self.tz)
            except ValueError:
                log.error("Ignoring invalid cron %r for %s", job.cron, job.label)
                continue
            self._sched.add_job(self._fire, trigger, args=[job.key], id=job.key, replace_existing=True)
        log.info("Schedule loaded:\n%s", self.describe() or "nothing scheduled")

    async def _fire(self, key: str) -> None:
        job = self.plan().get(key)  # re-read: membership may have changed since loading
        if job and job.source_ids:
            await self.engine.run(job.source_ids, trigger=f"{job.label} · {describe_schedule(job.cron)}",
                                  upload_from=job.upload_at)

    def next_runs(self) -> list[tuple[datetime, str, int]]:
        """[(next time, label, number of sources)], soonest first."""
        plan = self.plan()
        out = []
        for job in self._sched.get_jobs():
            p = plan.get(job.id)
            if p is None:
                continue
            # pending jobs (scheduler not started yet) have no next_run_time attribute
            when = getattr(job, "next_run_time", None) or job.trigger.get_next_fire_time(None, datetime.now(self.tz))
            out.append((when, p.label, len(p.source_ids)))
        far = datetime.max.replace(tzinfo=self.tz)
        return sorted(out, key=lambda x: x[0] or far)

    def describe(self) -> str:
        return "\n".join(f"{when:%Y-%m-%d %H:%M} — {label} ({n} sources)" if when else f"— {label}"
                         for when, label, n in self.next_runs())
