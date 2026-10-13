"""Logging that tells you *where* something happened.

Every log line carries the run and the source it belongs to, e.g.

    2026-10-14 08:00:12 INFO    [run#41 Hamrahan] backuper.engine: zipped HS_ChoobShar … 11.2 GB in 7m 02s (26.6 MB/s)

and every run also gets its own file, data/logs/runs/run-<id>.log, with extra
DEBUG detail (API calls, file choices, timings). The bot can send that file
from 📊 History (📄 Log) and `backuper log <id>` prints it.

How: two context variables (run, source) are set by the engine; they follow
asyncio tasks and threads started with asyncio.to_thread. A log-record factory
stamps them on every record, so no logger needs to know about runs.
"""

from __future__ import annotations

import contextvars
import logging
from pathlib import Path

run_var: contextvars.ContextVar[str] = contextvars.ContextVar("backuper_run", default="")
source_var: contextvars.ContextVar[str] = contextvars.ContextVar("backuper_source", default="")

FORMAT = "%(asctime)s %(levelname)-7s [%(ctx)s] %(name)s: %(message)s"
RUN_LOGS_KEEP = 300

_installed = False


def install() -> None:
    """Make every log record carry `ctx` ("run#12 Hamrahan" or "-") and `run_id`."""
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        run, src = run_var.get(), source_var.get()
        record.run_id = run
        record.ctx = " ".join(p for p in (f"run#{run}" if run else "", src) if p) or "-"
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


class RunFileHandler(logging.FileHandler):
    """Writes only the records of one run (DEBUG and up) to that run's own file."""

    def __init__(self, path: Path, run_id: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, encoding="utf-8")
        self.run_id = str(run_id)
        self.setLevel(logging.DEBUG)
        self.setFormatter(logging.Formatter(FORMAT))

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "run_id", "") == self.run_id and super().filter(record)


def run_log_path(log_dir: Path, run_id: int) -> Path:
    return log_dir / "runs" / f"run-{run_id}.log"


def start_run_log(log_dir: Path, run_id: int) -> RunFileHandler:
    install()
    handler = RunFileHandler(run_log_path(log_dir, run_id), run_id)
    logging.getLogger().addHandler(handler)
    return handler


def stop_run_log(handler: RunFileHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


def prune_run_logs(log_dir: Path, keep: int = RUN_LOGS_KEEP) -> None:
    files = sorted((log_dir / "runs").glob("run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
