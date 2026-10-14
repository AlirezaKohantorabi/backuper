"""Restart requests: the bot panel (🔄 Restart) and `backuper restart` ask the running service to restart.

They write a small request file into DATA_DIR; the service checks for it every few seconds and then
exits — right away, or once no backup is running — and systemd (`Restart=always`) starts it again about
15 seconds later, with whatever code is on disk. No sudo is needed: the request file is written by the
service's own user. Running by hand (not under systemd), the service simply stops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backuper.engine import Engine

log = logging.getLogger(__name__)

REQUEST_NAME = "restart-request.json"
NOTICE_NAME = "restart-notice.json"
POLL_SECONDS = 5


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def request_restart(data_dir: Path, *, now: bool, by: str, chat_id: int | None = None) -> None:
    """Ask the service to restart. now=False waits until no backup is running."""
    _write_json(data_dir / REQUEST_NAME, {"now": now, "by": by, "chat_id": chat_id, "at": time.time()})


def pending_request(data_dir: Path) -> dict[str, Any] | None:
    """The restart request waiting to be carried out, if any."""
    return _read_json(data_dir / REQUEST_NAME)


def clear_request(data_dir: Path) -> None:
    """Drop a request (at startup: a request made while the service was down is already fulfilled)."""
    (data_dir / REQUEST_NAME).unlink(missing_ok=True)


def take_notice(data_dir: Path) -> dict[str, Any] | None:
    """After a restart: who asked for it (so the bot can say it's back). Read once."""
    path = data_dir / NOTICE_NAME
    notice = _read_json(path)
    path.unlink(missing_ok=True)
    return notice


async def wait_for_restart(data_dir: Path, engine: Engine) -> dict[str, Any]:
    """Return the request once it's time to restart (now, or when no run is active)."""
    waiting_logged = False
    while True:
        req = pending_request(data_dir)
        if req is not None:
            if req.get("now") or not engine.runs:
                clear_request(data_dir)
                if req.get("chat_id"):
                    _write_json(data_dir / NOTICE_NAME, req)
                running = len(engine.runs)
                log.warning("restarting the service — requested by %s%s", req.get("by") or "?",
                            f" (stopping {running} running backup run(s))" if running else "")
                return req
            if not waiting_logged:
                log.info("restart requested by %s — waiting for %d running backup run(s) to finish",
                         req.get("by") or "?", len(engine.runs))
                req["waiting"] = True
                _write_json(data_dir / REQUEST_NAME, req)
                waiting_logged = True
        else:
            waiting_logged = False
        await asyncio.sleep(POLL_SECONDS)
