from __future__ import annotations

import asyncio
import ntpath
from typing import Any

from backuper.fields import BOOL, CHOICE, CREDENTIAL, LIST, UNC, Field
from backuper.models import Artifact, Entry, should_compress
from backuper.sources.base import SourceError, SourceType
from backuper.util import human_size, safe_filename

DEFAULT_EXCLUDE = ["~$*", "*.tmp", "Thumbs.db", "desktop.ini", "$RECYCLE.BIN", "System Volume Information"]


class SMBFolder(SourceType):
    type_id = "smb_folder"
    title = "Windows folder (SMB share)"
    category = "files"
    description = ("Zips a whole folder from a Windows share (streamed — no temporary copy). Files that are "
                   "locked by another program are skipped and listed as a warning.")
    fields = [
        Field("format", "Store as", CHOICE, default="zip", choices=("zip", "copy"),
              help="zip: one AES-256 encrypted zip (can also go to Telegram). copy: a plain folder in the archive "
                   "named <Source>_<date>_<time> with the files as they are — easy to browse and restore; archive "
                   "only, not encrypted. On the same server as the archive the copy happens inside that server."),
        Field("path", "Folder (network path)", UNC, example=r"\\192.168.10.35\D$\Edari",
              help=r"Admin shares like \\server\D$\folder work with an administrator account."),
        Field("credential", "Share credential", CREDENTIAL),
        Field("exclude", "Exclude patterns", LIST, required=False, default=DEFAULT_EXCLUDE,
              help="File or folder names to skip, wildcards allowed (e.g. *.iso, Temp)."),
        Field("skip_locked", "Skip locked files", BOOL, default=True,
              help="If off, a single open/locked file fails the whole backup."),
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        return config.get("path", "?")

    async def _list(self):
        smb = self.ctx.smb(self.cfg["credential"])
        files = await asyncio.to_thread(smb.list_files, self.cfg["path"], recursive=True,
                                        exclude=self.cfg.get("exclude") or [])
        return smb, files

    async def test(self) -> str:
        _, files = await self._list()
        total = sum(f.size for f in files)
        return f"Readable. {len(files)} files, {human_size(total)} in total (before compression)."

    async def collect(self) -> list[Artifact]:
        smb, files = await self._list()
        if not files:
            raise SourceError(f"{self.cfg['path']} is empty (or everything is excluded)")
        fmt = self.cfg.get("format") or "zip"
        # zip: entries live under the folder's own name; copy: the archive folder already carries the name
        root = "" if fmt == "copy" else safe_filename(ntpath.basename(self.cfg["path"].rstrip("\\")) or self.record.name) + "/"
        entries = [
            Entry(arcname=root + f.rel.replace("\\", "/"), size=f.size, mtime=f.mtime,
                  opener=smb.opener(f.path), compress=should_compress(f.name), source_path=f.path)
            for f in files
        ]
        return [Artifact(label="", entries=entries,
                         meta={"skip_unreadable": bool(self.cfg.get("skip_locked", True)), "format": fmt})]
