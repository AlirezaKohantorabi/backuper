# backuper/control.py

**Purpose:** restart the service from the bot (🔄 Restart, `/restart`) or the shell (`backuper restart`)
**without sudo**.

**How:** the request is a small file, `DATA_DIR/restart-request.json` (who asked, now or when idle, which
chat to tell). The running service checks for it every 5 s; when it's time it exits, and systemd
(`Restart=always` in the unit) starts it again ~15 s later with the code on disk. The service runs with
`NoNewPrivileges=true`, so `sudo systemctl restart` from inside it would not work anyway — exiting is the
clean way. Run by hand (not under systemd), the service just stops.

**Used by:** [cli.py](cli.md) (`restart`, archive-only `serve`), [bot/app.py](bot/app.md),
[bot/panel.py](bot/panel.md), [status.py](status.md) (shows a pending restart).

| Name | What it does |
|---|---|
| `REQUEST_NAME` / `NOTICE_NAME` / `POLL_SECONDS` | The request file, the "tell them I'm back" file, the check interval. |
| `_write_json` / `_read_json` | Atomic write / tolerant read of those files. |
| `request_restart` | Write a request: `now=True` stops running backups, otherwise waits until none is running. |
| `pending_request` | The request waiting to be carried out (`waiting: true` once the service has seen it and is waiting for backups). |
| `clear_request` | Drop it — at startup a leftover request is already fulfilled. |
| `take_notice` | After the restart: who asked (read once), so the bot can say it's back. |
| `wait_for_restart` | The service's watcher: returns when it's time to exit (logs who asked and how many runs it stops). |

**When backups finish vs. now:** "now" kills running backups; their unfinished files are removed at the next
start ([archive leftovers](destinations/archive.md)) and they run again at their next scheduled time.
