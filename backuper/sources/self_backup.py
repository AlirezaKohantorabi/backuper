"""Backs up Backuper itself: its database, its .env and the company configuration in local/.

Without these a dead bot server can't be rebuilt quickly: the database holds every source, group, setting and
(encrypted) password; .env holds the key that decrypts them; local/*.yaml is the company configuration that
isn't in git. The zip is AES-encrypted with the zip password like every other backup — keep that password
somewhere else too, because it is itself stored inside this backup.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from pathlib import Path

from backuper import __version__
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType
from backuper.util import human_size

APP_DIR = Path(__file__).resolve().parents[2]

RESTORE = """Backuper {version} (commit {commit}) — backup of the bot itself, made {when}.

Restore on a new Ubuntu server:
  1. git clone https://github.com/AlirezaKohantorabi/backuper.git ~/backuper-app && cd ~/backuper-app
     git checkout {commit}            # the same code (or stay on the newest)
  2. ./install.sh
  3. copy from this zip:  .env -> ~/backuper-app/.env      (chmod 600 .env)
                          data/backuper.db -> ~/backuper-app/data/backuper.db
                          local/*.yaml -> ~/backuper-app/local/
  4. backuper check   then   sudo systemctl restart backuper
The .env and the database belong together: the database's passwords can only be read with this .env's SECRET_KEY.
"""


def snapshot_database(src: Path, dst: Path) -> None:
    """Consistent copy of the live SQLite database (SQLite's online backup — safe while the service writes)."""
    dst.unlink(missing_ok=True)
    s, d = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(APP_DIR), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


class SelfBackup(SourceType):
    type_id = "self"
    title = "This Backuper server"
    category = "system"
    description = "The bot's own database, .env and company configuration — to rebuild this server."
    fields = []

    @classmethod
    def describe(cls, config: dict) -> str:
        return "database, .env, local/*.yaml"

    def _files(self) -> list[tuple[Path, str]]:
        app = self.ctx.app
        if app is None:
            raise SourceError("this source only works inside the Backuper service or command line")
        files = []
        if app.env_file and app.env_file.exists():
            files.append((app.env_file, ".env"))
        local = APP_DIR / "local"
        if local.is_dir():
            files += [(p, f"local/{p.name}") for p in sorted(local.iterdir()) if p.is_file()]
        return files

    async def test(self) -> str:
        files = self._files()
        lines = [f"Will back up: data/backuper.db ({human_size(self.ctx.app.db_path.stat().st_size)})"]
        lines += [f"  {arc} ({human_size(p.stat().st_size)})" for p, arc in files]
        return "\n".join(lines)

    async def collect(self) -> list[Artifact]:
        files = self._files()
        out = self.ctx.workdir / "self"
        out.mkdir(parents=True, exist_ok=True)
        db_copy = out / "backuper.db"
        await asyncio.to_thread(snapshot_database, self.ctx.app.db_path, db_copy)
        readme = out / "RESTORE.txt"
        readme.write_text(RESTORE.format(version=__version__, commit=await asyncio.to_thread(_commit),
                                         when=self.ctx.stamp), encoding="utf-8")
        entries = [Entry(arcname="data/backuper.db", size=db_copy.stat().st_size, mtime=db_copy.stat().st_mtime,
                         opener=lambda: open(db_copy, "rb")),
                   Entry(arcname="RESTORE.txt", size=readme.stat().st_size, mtime=readme.stat().st_mtime,
                         opener=lambda: open(readme, "rb"))]
        for path, arc in files:
            st = path.stat()
            entries.append(Entry(arcname=arc, size=st.st_size, mtime=st.st_mtime, opener=lambda p=path: open(p, "rb")))
        self.ctx.log.info("backing up itself: %s", ", ".join(e.arcname for e in entries))
        return [Artifact(label="", entries=entries, cleanup=[db_copy, readme])]
