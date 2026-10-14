"""Restart requests from the panel / CLI."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backuper import control


async def test_restart_now_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "POLL_SECONDS", 0.01)
    control.request_restart(tmp_path, now=False, by="tester", chat_id=42)
    req = await asyncio.wait_for(control.wait_for_restart(tmp_path, SimpleNamespace(runs={})), 2)
    assert req["by"] == "tester" and control.pending_request(tmp_path) is None
    assert control.take_notice(tmp_path)["chat_id"] == 42 and control.take_notice(tmp_path) is None


async def test_restart_waits_for_running_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "POLL_SECONDS", 0.01)
    engine = SimpleNamespace(runs={7: object()})
    control.request_restart(tmp_path, now=False, by="tester")
    waiter = asyncio.create_task(control.wait_for_restart(tmp_path, engine))
    await asyncio.sleep(0.1)
    assert not waiter.done() and control.pending_request(tmp_path)["waiting"]  # CLI can report "waiting"
    engine.runs.clear()
    await asyncio.wait_for(waiter, 2)
    assert control.take_notice(tmp_path) is None  # no chat to tell


async def test_restart_now_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "POLL_SECONDS", 0.01)
    control.request_restart(tmp_path, now=True, by="tester")
    await asyncio.wait_for(control.wait_for_restart(tmp_path, SimpleNamespace(runs={7: object()})), 2)
