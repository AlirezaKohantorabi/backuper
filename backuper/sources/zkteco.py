"""ZKTeco attendance terminal (the VF600 face terminal) — users, attendance log and face templates.

The reading is done by the owner's tested kit in `vendor/zk-backup/` (README there), used exactly as its
"Integration guide for the backuper bot" says: `zk_backup.py` runs as a separate program in its own Python
environment (`.venv-zk`, pyzk pinned to 0.9, because its read-only guard wraps a private pyzk method), with one
output folder per device on this server; the result is read from the `last_run.json` it writes. Nothing here
imports the kit or speaks the device protocol, and nothing ever writes to a device (`zk_restore.py` is a
human-only tool and is never run by the bot).

The new snapshot (users.csv, attendance.csv, faces.json, info.json, SHA256SUMS) is checked against its own
SHA256SUMS, then zipped — AES-encrypted like every backup, because it holds PINs, card numbers and face
templates — together with attendance_master.csv (every punch ever seen) and the run's last_run.json/state.json.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from backuper.fields import BOOL, HOST, INT, SECRET, TEXT, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType

APP_DIR = Path(__file__).resolve().parents[2]
KIT_DIR = APP_DIR / "vendor" / "zk-backup"
TOOL_TIMEOUT = 20 * 60   # the guide: ~60 s for 237 users with faces; 20 minutes at most
EXIT_LOCKED = 3          # another run holds the device folder's lock: skipped, not a failure


def zk_python() -> Path:
    """The Python with pyzk 0.9 (install.sh creates .venv-zk); $ZK_PYTHON overrides it."""
    return Path(os.environ.get("ZK_PYTHON") or APP_DIR / ".venv-zk" / "bin" / "python")


def verify_snapshot(snap: Path) -> int:
    """Check every file of a snapshot against its SHA256SUMS (like `sha256sum -c`). Returns the file count."""
    sums = snap / "SHA256SUMS"
    if not sums.is_file():
        raise SourceError(f"snapshot {snap.name} has no SHA256SUMS")
    count = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        h = hashlib.sha256()
        try:
            with open(snap / name, "rb") as f:
                for block in iter(lambda f=f: f.read(1 << 20), b""):
                    h.update(block)
        except OSError as e:
            raise SourceError(f"snapshot {snap.name}: {name} unreadable ({e})") from e
        if h.hexdigest() != digest.strip():
            raise SourceError(f"snapshot {snap.name}: {name} doesn't match its SHA256SUMS")
        count += 1
    return count


class ZKTeco(SourceType):
    type_id = "zkteco"
    title = "ZKTeco attendance device"
    category = "devices"
    description = "Users, punches and face templates of a ZKTeco terminal (read-only, via vendor/zk-backup)."
    fields = [
        Field("host", "Device IP", HOST, example="192.168.10.113",
              help="The terminal's address (device menu: Comm. > Network)."),
        Field("port", "Port", INT, default=4370, min=1, max=65535, tags=frozenset({"advanced"})),
        Field("comm_key", "Comm Key", SECRET, required=False, default="",
              help="The device's Comm Key (a number), if one is set. Empty = none."),
        Field("faces", "Face templates", BOOL, default=True,
              help="Also read the face templates (needed to restore a face terminal). On for the VF600."),
        Field("keep_snapshots", "Snapshots kept on this server", INT, default=14, min=1, max=3650,
              help="zk_backup's own snapshots in data/zk/ (about 10 MB each with faces). The punch list "
                   "attendance_master.csv is never shortened."),
        Field("encoding", "Name encoding", TEXT, required=False, default="UTF-8", tags=frozenset({"advanced"}),
              help="Try cp1256 if names look garbled."),
        Field("udp", "Force UDP", BOOL, default=False, tags=frozenset({"advanced"}),
              help="Normally TCP is used if the port is open, otherwise UDP."),
        Field("timeout", "Per-packet timeout (s)", INT, default=20, min=2, max=300, tags=frozenset({"advanced"})),
    ]

    def _out_dir(self) -> Path:
        """One folder per device (the tool refuses a different serial in the same folder)."""
        app = self.ctx.app
        if app is None:
            raise SourceError("this source only works inside the Backuper service or command line")
        return app.data_dir / "zk" / f"source-{self.record.id}"

    def _command(self, out: Path) -> tuple[list[str], list[str]]:
        """(command, the same with the Comm Key masked for the log)."""
        cmd = [str(zk_python()), str(KIT_DIR / "zk_backup.py"), self.cfg["host"], "--out", str(out),
               "--port", str(int(self.cfg.get("port") or 4370)), "--keep", str(int(self.cfg.get("keep_snapshots") or 14)),
               "--timeout", str(int(self.cfg.get("timeout") or 20)),
               "--encoding", self.cfg.get("encoding") or "UTF-8"]
        if self.cfg.get("faces", True):
            cmd.append("--faces")
        if self.cfg.get("udp"):
            cmd.append("--udp")
        key = str(self.cfg.get("comm_key") or "").strip()
        if key and key != "0":
            if not key.isdigit():
                raise SourceError("the Comm Key must be a number")
            return [*cmd, "--password", key], [*cmd, "--password", "***"]
        return cmd, cmd

    def _check_installed(self) -> None:
        if not (KIT_DIR / "zk_backup.py").is_file():
            raise SourceError(f"{KIT_DIR / 'zk_backup.py'} is missing")
        if not zk_python().is_file():
            raise SourceError(f"{zk_python()} is missing — run ./install.sh (it creates .venv-zk with pyzk 0.9)")

    async def test(self) -> str:
        self._check_installed()
        proc = await asyncio.create_subprocess_exec(str(zk_python()), str(KIT_DIR / "zk_backup.py"), "--help",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode:
            raise SourceError(f"zk_backup.py can't start: {out.decode(errors='replace').strip()[-300:]}")
        host, port = self.cfg["host"], int(self.cfg.get("port") or 4370)
        lines = [f"zk_backup.py and pyzk are installed ({zk_python()})."]
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 8)
            writer.close()
            lines.append(f"The device answers on {host}:{port} (TCP).")
        except (OSError, asyncio.TimeoutError) as e:
            raise SourceError(f"{host}:{port} can't be reached over TCP ({e.__class__.__name__}: {e}) — "
                              "device off, wrong IP, or a firewall in between") from e
        last = self._out_dir() / "last_run.json"
        if last.is_file():
            doc = json.loads(last.read_text(encoding="utf-8"))
            lines.append(f"Last backup: {doc.get('status')} at {doc.get('finished')} "
                         f"({doc.get('users', '?')} users, {doc.get('faces_saved', '?')} faces).")
        return "\n".join(lines)

    async def collect(self) -> list[Artifact]:
        self._check_installed()
        out = self._out_dir()
        out.mkdir(parents=True, exist_ok=True)
        os.chmod(out.parent, 0o700)
        cmd, shown = self._command(out)
        self.ctx.log.info("running %s", " ".join(shown))
        started = time.time()
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT,
                                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SourceError(f"zk_backup.py didn't finish in {TOOL_TIMEOUT // 60} minutes") from None
        output = raw.decode(errors="replace").splitlines()
        for line in output:
            self.ctx.log.debug("zk_backup: %s", line)
        code = proc.returncode
        if code == EXIT_LOCKED:
            self.ctx.note("another zk_backup run is using this device's folder — skipped this time")
            return []
        last = out / "last_run.json"
        if not last.is_file() or last.stat().st_mtime < started - 1:
            raise SourceError(f"zk_backup.py exited with {code} without a new last_run.json: "
                              + " / ".join(output[-3:]))
        doc = json.loads(last.read_text(encoding="utf-8"))
        status = doc.get("status")
        if status not in ("ok", "warning"):
            reasons = doc.get("errors") or doc.get("warnings") or output[-3:]
            raise SourceError(f"{status} (exit {code}): " + "; ".join(reasons))
        for w in doc.get("warnings") or []:
            self.ctx.warn(f"device: {w}")
        snap = Path(doc["snapshot"])
        checked = await asyncio.to_thread(verify_snapshot, snap)
        drift = doc.get("clock_drift_seconds")
        self.ctx.note(f"serial {doc.get('serial')}: {doc.get('users')} users, {doc.get('punches_on_device')} punches on "
                      f"the device ({doc.get('new_punches')} new, {doc.get('master_total_punches')} in the master list), "
                      f"{doc.get('faces_saved')}/{doc.get('faces_on_device')} faces"
                      + (f", device clock {drift:+d}s" if isinstance(drift, int) else ""))
        self.ctx.log.info("snapshot %s: %d file(s) match SHA256SUMS", snap.name, checked)

        entries = []
        for path in sorted(p for p in snap.rglob("*") if p.is_file()):
            entries.append(self._entry(path, f"snapshots/{snap.name}/{path.relative_to(snap).as_posix()}"))
        for name in ("attendance_master.csv", "last_run.json", "state.json"):
            if (out / name).is_file():
                entries.append(self._entry(out / name, name))
        return [Artifact(label="", entries=entries, meta={"snapshot": str(snap)})]

    @staticmethod
    def _entry(path: Path, arcname: str) -> Entry:
        st = path.stat()
        return Entry(arcname=arcname, size=st.st_size, mtime=st.st_mtime, opener=lambda p=path: open(p, "rb"))
