# backuper/scheduler.py

**Purpose:** decides *when* runs happen and starts them. Uses APScheduler's asyncio scheduler on the
bot server's clock in the configured timezone (so servers with a wrong clock don't matter).

**Uses:** [db.py](db.md) (groups, sources, one-time runs), [engine.py](engine.md) (`run`).
**Used by:** [bot/app.py](bot/app.md) and [cli.py](cli.md) (archive-only mode) start it;
[bot/panel.py](bot/panel.md) calls `reload()` after every change and shows `next_runs()`.

## Rules

1. **Group** (enabled, with members): at each of its times, one run with all its enabled sources.
   That run uploads smallest-first; if the group has *Upload from*, uploads wait until then.
2. **Source outside a group** with *Own time*: one run with just that source at those times.
3. Otherwise: manual only (▶️ Run now).
4. **One-time runs:** a date-trigger job per pending entry; it runs once and uploads right away.
   If the service was down at that time, it still runs if less than an hour late (`ONE_OFF_GRACE`),
   otherwise it's marked *missed*.
5. Jobs coalesce and never overlap with themselves (`max_instances=1`), and a job up to an hour late
   (e.g. after a restart) still fires.

## Classes and functions

| Name | What it does |
|---|---|
| `ONE_OFF_GRACE` | 1 hour — how late a one-time run may still start. |
| `PlannedJob` | One cron job: key, cron line, label, source ids, upload time. |
| `Scheduler` | Wraps `AsyncIOScheduler`. |
| `Scheduler.__init__` | Creates the scheduler in the configured timezone. |
| `Scheduler.start` / `Scheduler.shutdown` | Load jobs and start / stop. |
| `Scheduler.plan` | Computes the cron jobs from groups and own times (rules 1–3). Re-computed at every fire, so membership changes take effect immediately. |
| `Scheduler.reload` | Rebuilds every job (cron + one-time); marks one-time runs that are too late as missed. |
| `Scheduler._fire` | A cron job fired: run its sources (with the group's upload time). |
| `Scheduler._fire_once` | A one-time job fired: mark running, run, mark done (or failed) with the run id. |
| `Scheduler._add_once_job` | Adds the job for one one-time run. |
| `Scheduler.retry_failed` | After a scheduled or one-time run: its **failed** sources become a new one-time run `retry_failed_after` minutes later (default 60), at most `retry_failed_times` times (default 3) — for power cuts, link outages, reboots. Listed under "Next" in the status file. |
| `Scheduler.next_runs` | `[(time, label, number of sources)]` soonest first — shown on the main menu and the ⏰ Schedule page. |
| `Scheduler.describe` | The same as text (logged at startup). |
