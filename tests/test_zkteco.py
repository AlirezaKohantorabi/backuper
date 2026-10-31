"""ZKTeco attendance device source: the vendored zk_backup.py run as a black box, its last_run.json read."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pyzipper
import pytest

from backuper.engine import Engine
from backuper.models import FAILED, OK, SKIPPED, WARNING
from backuper.sources import zkteco

FAKE_TOOL = textwrap.dedent('''
    """Stand-in for zk_backup.py: behaves as FAKE_MODE says, writes what the real one writes."""
    import hashlib, json, os, sys, time
    out = sys.argv[sys.argv.index("--out") + 1]
    mode = os.environ.get("FAKE_MODE", "ok")
    os.makedirs(os.path.join(out, "snapshots"), exist_ok=True)
    open(os.path.join(out, "args.txt"), "w").write(" ".join(sys.argv[1:]))
    if mode == "locked":
        sys.exit(3)
    if mode == "stale":
        sys.exit(1)                        # an old last_run.json from earlier stays behind
    doc = {"status": mode if mode in ("ok", "warning", "read_error") else "ok", "exit_code": 0,
           "device_ip": sys.argv[1], "warnings": [], "errors": []}
    if mode == "read_error":
        doc.update(exit_code=1, errors=["READ FAILED: TimeoutError: timed out"])
    else:
        snap = os.path.join(out, "snapshots", "2026-10-31_030001")
        os.makedirs(snap, exist_ok=True)
        files = {"users.csv": "uid,user_id,name\\n1,101,Ali\\n", "attendance.csv": "user_id,timestamp\\n",
                 "faces.json": "{}", "info.json": "{}"}
        for n, t in files.items():
            open(os.path.join(snap, n), "w").write(t)
        sums = "".join(f"{hashlib.sha256(t.encode()).hexdigest()}  {n}\\n" for n, t in sorted(files.items()))
        if mode == "badsum":
            sums = sums.replace(sums[:8], "00000000", 1)
        open(os.path.join(snap, "SHA256SUMS"), "w").write(sums)
        open(os.path.join(out, "attendance_master.csv"), "w").write("user_id,timestamp\\n")
        open(os.path.join(out, "state.json"), "w").write("{}")
        doc.update(snapshot=snap, serial="6650154800185", users=1, punches_on_device=0, new_punches=0,
                   master_total_punches=0, faces_saved=1, faces_on_device=1, clock_drift_seconds=4)
        if mode == "warning":
            doc["warnings"] = ["device clock is +140s off this host - punches are stamped with the device clock"]
    open(os.path.join(out, "last_run.json"), "w").write(json.dumps(doc))
    sys.exit(doc["exit_code"])
''')


@pytest.fixture
def fake_kit(tmp_path, monkeypatch):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "zk_backup.py").write_text(FAKE_TOOL)
    monkeypatch.setattr(zkteco, "KIT_DIR", kit)
    monkeypatch.setenv("ZK_PYTHON", sys.executable)
    return kit


def _source(store, tmp_path, **cfg):
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.set_setting("zip_password", "pw")
    return store.create_source("Paliz Device", "zkteco", {"host": "192.168.10.113", "telegram": False, **cfg},
                               {"comm_key"})


async def test_ok_run_archives_the_verified_snapshot(cfg, store, tmp_path, fake_kit, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    sid = _source(store, tmp_path, comm_key="1234", keep_snapshots=14)
    run_id = await Engine(cfg, store, None).run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == OK and "1 users" in item["message"] and "6650154800185" in item["message"]
    args = (cfg.data_dir / "zk" / f"source-{sid}" / "args.txt").read_text()
    assert "--keep 14" in args and "--faces" in args and "--password 1234" in args
    with pyzipper.AESZipFile(next((tmp_path / "arch").rglob("Paliz-Device_*.zip"))) as z:
        z.setpassword(b"pw")
        assert sorted(z.namelist()) == sorted([
            "attendance_master.csv", "last_run.json", "state.json",
            *(f"snapshots/2026-10-31_030001/{n}" for n in
              ("SHA256SUMS", "attendance.csv", "faces.json", "info.json", "users.csv"))])


@pytest.mark.parametrize(("mode", "status", "text"), [
    ("warning", WARNING, "device clock is +140s"),
    ("read_error", FAILED, "READ FAILED"),
    ("locked", SKIPPED, "another zk_backup run"),
    ("badsum", FAILED, "doesn't match its SHA256SUMS"),
    ("stale", FAILED, "without a new last_run.json"),
])
async def test_tool_outcomes_become_source_statuses(cfg, store, tmp_path, fake_kit, monkeypatch, mode, status, text):
    sid = _source(store, tmp_path)
    if mode == "stale":                                   # an old successful run's file must not be trusted
        old = cfg.data_dir / "zk" / f"source-{sid}"
        old.mkdir(parents=True)
        (old / "last_run.json").write_text('{"status": "ok", "snapshot": "/nope"}')
        past = time.time() - 3600
        os.utime(old / "last_run.json", (past, past))
    monkeypatch.setenv("FAKE_MODE", mode)
    run_id = await Engine(cfg, store, None).run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == status and text in item["message"]


async def test_comm_key_must_be_a_number(cfg, store, tmp_path, fake_kit):
    _source(store, tmp_path, comm_key="abc")
    run_id = await Engine(cfg, store, None).run(trigger="t")
    assert "must be a number" in store.run_items(run_id)[0]["message"]


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not zkteco.zk_python().is_file(), reason=".venv-zk (pyzk 0.9) not installed")
async def test_real_kit_against_its_device_simulator(cfg, store, tmp_path):
    """The real zk_backup.py, unchanged, against the kit's own simulated VF600 (5 users, faces like VX7.0)."""
    port, audit = _free_udp_port(), tmp_path / "audit.log"
    sim = subprocess.Popen([str(zkteco.zk_python()), str(zkteco.KIT_DIR / "tests" / "fake_zk.py"), str(port),
                            str(audit), "faces150", "vx7"])
    try:
        time.sleep(0.5)
        _source(store, tmp_path, host="127.0.0.1", port=port, udp=True, timeout=3)
        run_id = await Engine(cfg, store, None).run(trigger="t")
    finally:
        sim.terminate()
        sim.wait()
    item = store.run_items(run_id)[0]
    assert item["status"] == OK, item["message"]
    assert "5 users" in item["message"] and "200 punches" in item["message"] and "4/4 faces" in item["message"]
    assert "VIOLATION" not in audit.read_text()            # only whitelisted reads reached the "device"
    with pyzipper.AESZipFile(next((tmp_path / "arch").rglob("Paliz-Device_*.zip"))) as z:
        z.setpassword(b"pw")
        names = z.namelist()
        assert "attendance_master.csv" in names and any(n.endswith("/faces.json") for n in names)
    assert Path(cfg.data_dir / "zk").stat().st_mode & 0o077 == 0  # device data folder is owner-only
