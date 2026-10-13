# backuper/logctx.py

**Purpose:** make logs useful for troubleshooting. Every log line says **which run and which source** it
belongs to, and every run gets **its own log file** with extra detail.

```
2026-10-14 08:00:12 INFO    [run#41 Hamrahan] backuper.source.Hamrahan: HS_ChoobShar: taking HS_ChoobShar_14050722_081831.bak (12133496320 bytes, 2026-10-14 08:18)
2026-10-14 08:07:40 INFO    [run#41 Hamrahan] backuper.engine: zipped Hamrahan_HS_ChoobShar_1405-07-22_0800.zip: 11.3 GB → 11.2 GB in 7m 28s (25.8 MB/s)
2026-10-14 08:07:41 ERROR   [run#41 Factory] backuper.engine: FAILED: Permission denied for user backuper on host 192.168.17.254
```

**Where logs go:**

| Place | Level | Content |
|---|---|---|
| journald (`journalctl -u backuper -f`) | `LOG_LEVEL` (INFO) | everything, tagged |
| `data/logs/backuper.log` (10 MB × 5, rotating) | `LOG_LEVEL` | the same |
| `data/logs/runs/run-<id>.log` | DEBUG | only that run: every step, choice, timing, API call, retry, deletion, error with traceback |

The run log is sent by the bot (📊 History → run → **📄 Log**) or printed with `backuper log [<id>]`.
The newest 300 run logs are kept.

**How it works:** two `contextvars` (`run_var`, `source_var`) are set by the [engine](engine.md) — for
the run, and per source task. Context variables follow asyncio tasks and `asyncio.to_thread`, so lines
written deep inside SMB/SQL/FTP code still carry the right tags. A log-record factory copies them onto every
record (`ctx`, `run_id`); no logger needs to know about runs. The root logger lets DEBUG through; the
console/file handlers stay at `LOG_LEVEL`; only the run file takes DEBUG.

**Used by:** [cli.py](cli.md) (`setup_logging`, `backuper log`), [engine.py](engine.md) (start/stop the run
log, set the context), [bot/panel.py](bot/panel.md) (📄 Log).

| Name | What it does |
|---|---|
| `run_var` / `source_var` | Current run id / source name (empty outside runs). |
| `FORMAT` | `%(asctime)s %(levelname)-7s [%(ctx)s] %(name)s: %(message)s`. |
| `RUN_LOGS_KEEP` | 300 run logs are kept. |
| `install` | Installs the record factory that adds `ctx` and `run_id` to every log record (idempotent). |
| `RunFileHandler` | File handler that only accepts records of one run. |
| `RunFileHandler.__init__` | Opens `run-<id>.log`, DEBUG level, the tagged format. |
| `RunFileHandler.filter` | Accepts a record only if its `run_id` matches. |
| `run_log_path` | `data/logs/runs/run-<id>.log`. |
| `start_run_log` / `stop_run_log` | Attach the run's handler to the root logger / detach and close it. |
| `prune_run_logs` | Delete run logs beyond the newest N. |
