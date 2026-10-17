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
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from backuper.db import Store
from backuper.engine import Engine
from backuper.fields import describe_schedule
from backuper.models import FAILED

log = logging.getLogger(__name__)
ONE_OFF_GRACE = timedelta(hours=1)  # a one-time run missed while the service was down still runs within this


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

    def _add_once_job(self, oid: int, when: datetime) -> None:
        self._sched.add_job(self._fire_once, DateTrigger(run_date=when, timezone=self.tz), args=[oid],
                            id=f"once:{oid}", replace_existing=True,
                            misfire_grace_time=int(ONE_OFF_GRACE.total_seconds()))

    def reload(self) -> None:
        self._sched.remove_all_jobs()
        for job in self.plan().values():
            try:
                trigger = CronTrigger.from_crontab(job.cron, timezone=self.tz)
            except ValueError:
                log.error("Ignoring invalid cron %r for %s", job.cron, job.label)
                continue
            self._sched.add_job(self._fire, trigger, args=[job.key], id=job.key, replace_existing=True)
        now = datetime.now(self.tz)
        for o in self.store.list_one_offs():
            if o.run_at < now - ONE_OFF_GRACE:
                self.store.set_one_off(o.id, "missed")
                log.warning("One-time run %r at %s was missed (service was down)", o.label, o.run_at)
                continue
            self._add_once_job(o.id, max(o.run_at, now + timedelta(seconds=5)))
        log.info("Schedule loaded:\n%s", self.describe() or "nothing scheduled")

    async def _fire(self, key: str) -> None:
        job = self.plan().get(key)  # re-read: membership may have changed since loading
        if job and job.source_ids:
            run_id = await self.engine.run(job.source_ids, trigger=f"{job.label} · {describe_schedule(job.cron)}",
                                           upload_from=job.upload_at)
            self.retry_failed(run_id, 1)

    async def _fire_once(self, oid: int) -> None:
        o = self.store.get_one_off(oid)
        if o is None or o.status != "pending":
            return
        self.store.set_one_off(oid, "running")
        ids = [i for i in o.source_ids if self.store.get_source(i)]
        try:
            run_id = await self.engine.run(ids, trigger=f"one-time · {o.label}")  # uploads right away
            self.store.set_one_off(oid, "done", run_id)
            self.retry_failed(run_id, o.retry + 1)
        except Exception:
            log.exception("one-time run %s failed", oid)
            self.store.set_one_off(oid, "failed")

    def retry_failed(self, run_id: int, attempt: int) -> int | None:
        """Schedule another try of a run's FAILED sources (a power cut, an outage of the link to another site,
        a server rebooting…): `retry_failed_after` minutes later, at most `retry_failed_times` times.
        Returns the new one-time run's id, or None."""
        after = int(self.store.get_setting("retry_failed_after") or 0)
        times = int(self.store.get_setting("retry_failed_times") or 0)
        if after <= 0 or attempt > times:
            return None
        failed = [it for it in self.store.run_items(run_id) if it["status"] == FAILED and it["source_id"]]
        if not failed:
            return None
        when = datetime.now(self.tz) + timedelta(minutes=after)
        names = ", ".join(it["source_name"] for it in failed)
        oid = self.store.add_one_off(f"retry {attempt}/{times} after run #{run_id}: {names}",
                                     [it["source_id"] for it in failed], when, None, retry=attempt)
        if self._sched.running:
            self._add_once_job(oid, when)
        log.warning("run #%d: %d failed source(s) (%s) will be retried at %s (try %d of %d)", run_id, len(failed),
                    names, f"{when:%H:%M}", attempt, times)
        return oid

    def next_runs(self) -> list[tuple[datetime, str, int]]:
        """[(next time, label, number of sources)], soonest first."""
        plan = self.plan()
        once = {f"once:{o.id}": o for o in self.store.list_one_offs()}
        out = []
        for job in self._sched.get_jobs():
            if job.id in once:
                o = once[job.id]
                out.append((o.run_at, f"⏱ once: {o.label}", len(o.source_ids)))
                continue
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
