# backuper/status.py

**Purpose:** let people see that big backups are running — and how far along they are — **without the
bot** (archive-only mode) and without logging into the server. The service keeps a plain-text status file
up to date in two places:

| File | How to look at it |
|---|---|
| `<archive>\_backuper-status.txt` (e.g. `E:\Backuper\_backuper-status.txt`) | open it in Explorer/Notepad on the file server |
| `data/status.txt` on the bot server | `backuper status` |

Example while Edari is being copied:

```
Backuper status — updated 1405/07/22 07:12

RUNNING (refreshed about every 20 s)

⏳ Run #12 (group Files · daily 07:00) — collecting, 12m 03s
• Edari: copying → archive 41% · 8.7 GB / 21.2 GB · 52.0 MB/s · ~4m 10s left
• ✓ Finance: ready
• iCan-Upload: collecting (12m 01s)

Last runs:
  ✅ #11 ok — 1405/07/21 17:08 (cli), 4s, 150.1 KB
Next:
  1405/07/22 08:00 — group Daily, 14 source(s)
```

Refresh: every ~20 s while a run is active; when idle only when something changes (at least every 10 min).
The file is UTF-8 with a BOM so Notepad shows Persian names correctly. Writing it never affects a backup —
errors are only logged at DEBUG.

**Used by:** started as a background task by [bot/app.py](bot/app.md) and by [cli.py](cli.md)'s archive-only
mode; reads [engine.py](engine.md) (`runs`, `RunProgress.render(html=False)`), [db.py](db.md) (last runs),
[scheduler.py](scheduler.md) (next runs); writes through [destinations/archive.py](destinations/archive.md)
(`write_status`).

| Name | What it does |
|---|---|
| `log` | Logger. |
| `BUSY_INTERVAL`, `IDLE_INTERVAL`, `IDLE_HEARTBEAT`, `STATUS_NAME` | 20 s refresh while running, 60 s check when idle, rewrite at least every 10 min, the archive file name. |
| `StatusWriter` | Composes and writes the status. |
| `StatusWriter.__init__` | Config, store, engine, scheduler (optional). |
| `StatusWriter.compose` | (header with the time, body): running runs with per-source progress, or "Idle"; last 5 runs; next 5 scheduled runs; mode. |
| `StatusWriter._problems` | "Problems in those runs": every failed / partial / warning source of the last 3 finished runs with its reason. |
| `StatusWriter._run_line` | One line per past run: icon, id, status, Jalali start time, trigger, duration, size. |
| `StatusWriter.write_once` | Writes `data/status.txt` (atomic rename) and the archive copy when the body changed or the refresh/heartbeat is due. |
| `StatusWriter.run` | The background loop. |
