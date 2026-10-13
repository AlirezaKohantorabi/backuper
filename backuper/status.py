"""A live, human-readable status file — so you can see big backups progressing without the bot.

Written to two places while the service runs:
  * data/status.txt on the bot server (`backuper status` prints it), and
  * <archive>\\_backuper-status.txt (e.g. E:\\Backuper\\_backuper-status.txt) — open it in Explorer.

Every ~20 s while a run is active (each source's step, %, size done, speed, time left, and the
upload in progress); when idle, only when something changed (and at least every 10 minutes):
"Idle", the last runs and the next scheduled ones.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from backuper import jalali
from backuper.config import AppConfig
from backuper.db import Store
from backuper.engine import Engine
from backuper.models import STATUS_ICON
from backuper.util import human_duration, human_size

log = logging.getLogger(__name__)

BUSY_INTERVAL = 20
IDLE_INTERVAL = 60
IDLE_HEARTBEAT = 600
STATUS_NAME = "_backuper-status.txt"


class StatusWriter:
    def __init__(self, cfg: AppConfig, store: Store, engine: Engine, scheduler=None):
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.scheduler = scheduler
        self.local_path = cfg.data_dir / "status.txt"
        self._last_body = ""
        self._last_write = 0.0

    def compose(self) -> tuple[str, str]:
        """(header line with the time, body). The body alone decides whether anything changed."""
        tz = self.cfg.timezone
        header = f"Backuper status — updated {jalali.pretty(jalali.now_jalali(tz))}"
        lines: list[str] = []
        if self.engine.runs:
            lines.append(f"RUNNING (refreshed about every {BUSY_INTERVAL} s)")
            for prog in list(self.engine.runs.values()):
                lines += ["", prog.render(html=False)]
        else:
            lines.append("Idle — no backup running.")
        lines += ["", "Last runs:"]
        for r in self.store.list_runs(5):
            lines.append("  " + self._run_line(r))
        if self.scheduler is not None:
            nxt = self.scheduler.next_runs()[:5]
            if nxt:
                lines += ["", "Next:"]
                for when, label, n in nxt:
                    lines.append(f"  {jalali.pretty(jalali.to_jalali(when.astimezone(tz)))} — {label}, {n} source(s)")
        mode = "Telegram uploads on" if self.engine.sender is not None else "ARCHIVE-ONLY (no Telegram uploads)"
        lines += ["", f"Mode: {mode}. Details per run: `backuper log <run>` or 📊 History → 📄 Log."]
        return header, "\n".join(lines)

    def _run_line(self, r) -> str:
        icon = STATUS_ICON.get(r["status"], "⏳" if r["status"] == "running" else "•")
        took = ""
        if r["finished_at"]:
            try:
                secs = (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds()
                took = f", {human_duration(secs)}"
            except ValueError:
                pass
        started = datetime.fromisoformat(r["started_at"])  # stored in UTC with offset
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        started = started.astimezone(self.cfg.timezone)
        return (f"{icon} #{r['id']} {r['status']} — {jalali.pretty(jalali.to_jalali(started))} ({r['trigger']})"
                f"{took}, {human_size(r['total_bytes'])}")

    def write_once(self, force: bool = False) -> bool:
        """Write both files if the content changed (or on the idle heartbeat). Returns True if written."""
        header, body = self.compose()
        due = time.monotonic() - self._last_write > (BUSY_INTERVAL if self.engine.runs else IDLE_HEARTBEAT)
        if not force and body == self._last_body and not due:
            return False
        text = header + "\n\n" + body + "\n"
        tmp = self.local_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.local_path)
        try:
            self.engine.archive.write_status(STATUS_NAME, text)
        except Exception as e:  # noqa: BLE001 - the archive may be unreachable for a moment
            log.debug("couldn't write the status file to the archive: %s", e)
        self._last_body, self._last_write = body, time.monotonic()
        return True

    async def run(self) -> None:
        """Forever: refresh often while running, rarely when idle."""
        while True:
            try:
                await asyncio.to_thread(self.write_once)
            except Exception:  # noqa: BLE001
                log.debug("status file update failed", exc_info=True)
            await asyncio.sleep(BUSY_INTERVAL if self.engine.runs else IDLE_INTERVAL)
