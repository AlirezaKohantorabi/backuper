r"""The long-term archive: <archive_path>\<jalali date>\<zip files>.

`archive_path` is either a network share (\\server\share\folder, written over
SMB) or a local directory on this server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TypeVar

from backuper import jalali
from backuper.db import Store
from backuper.transport.smb import SMB, SMBError
from backuper.transport.smb import join as unc_join
from backuper.util import human_size

log = logging.getLogger(__name__)
T = TypeVar("T")


class ArchiveError(RuntimeError):
    pass


class Archive:
    def __init__(self, store: Store):
        self.store = store

    @property
    def root(self) -> str:
        return (self.store.get_setting("archive_path") or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.root)

    @property
    def is_smb(self) -> bool:
        return self.root.startswith("\\\\") or self.root.startswith("//")

    def _smb(self) -> SMB:
        name = self.store.get_setting("archive_credential") or ""
        cred = self.store.get_credential_by_name(name) if name else None
        if name and cred is None:
            raise ArchiveError(f"archive credential '{name}' doesn't exist")
        return SMB(cred.username if cred else "", cred.password if cred else "")

    def _root_unc(self) -> str:
        return self.root.replace("/", "\\")

    # -- operations (run in threads) ---------------------------------------------
    def _store_sync(self, local: Path, folder: str, name: str, progress: Callable[[int], None] | None) -> str:
        rel = f"{folder}/{name}"
        if self.is_smb:
            smb = self._smb()
            d = unc_join(self._root_unc(), folder)
            smb.makedirs(d)
            smb.upload(local, unc_join(d, name), progress)
        else:
            d = Path(self.root) / folder
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (name + ".uploading")
            shutil.copyfile(local, tmp)
            os.replace(tmp, d / name)
        return rel

    def _write_direct_sync(self, folder: str, name: str, write: Callable[[BinaryIO], T]) -> tuple[str, int, T]:
        rel = f"{folder}/{name}"
        if self.is_smb:
            smb = self._smb()
            d = unc_join(self._root_unc(), folder)
            smb.makedirs(d)
            final = unc_join(d, name)
            tmp = final + ".uploading"
            try:
                with smb.open_write(tmp) as f:
                    result = write(f)
                size = smb.stat(tmp).st_size
                smb.replace(tmp, final)
            except BaseException:
                try:
                    smb.remove(tmp)
                except SMBError:
                    pass
                raise
        else:
            d = Path(self.root) / folder
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (name + ".uploading")
            try:
                with open(tmp, "wb") as f:
                    result = write(f)
                size = tmp.stat().st_size
                os.replace(tmp, d / name)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        return rel, size, result

    async def write_direct(self, folder: str, name: str, write: Callable[[BinaryIO], T]) -> tuple[str, int, T]:
        """Create <folder>/<name> in the archive by streaming into it with `write(fileobj)`
        — no local copy. Used for archive-only sources that are too big for staging.
        Returns (relative path, size, write's return value)."""
        return await asyncio.to_thread(self._write_direct_sync, folder, name, write)

    def _delete_sync(self, rel: str) -> bool:
        folder, name = rel.split("/", 1)
        try:
            if self.is_smb:
                self._smb().remove(unc_join(self._root_unc(), folder, name))
            else:
                (Path(self.root) / folder / name).unlink()
            return True
        except (FileNotFoundError, SMBError):
            return False  # already gone (e.g. its date folder was pruned)

    async def delete(self, rel: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, rel)

    async def store_file(self, local: Path, folder: str, name: str,
                         progress: Callable[[int], None] | None = None) -> str:
        """Copy a finished zip into the archive. Returns its relative path."""
        return await asyncio.to_thread(self._store_sync, local, folder, name, progress)

    def _fetch_sync(self, rel: str, local: Path) -> None:
        folder, name = rel.split("/", 1)
        local.parent.mkdir(parents=True, exist_ok=True)
        if self.is_smb:
            self._smb().download(unc_join(self._root_unc(), folder, name), local)
        else:
            shutil.copyfile(Path(self.root) / folder / name, local)

    async def fetch(self, rel: str, local: Path) -> None:
        await asyncio.to_thread(self._fetch_sync, rel, local)

    def _list_date_folders_sync(self) -> list[str]:
        if self.is_smb:
            names = self._smb().list_dirs(self._root_unc())
        else:
            p = Path(self.root)
            names = [c.name for c in p.iterdir() if c.is_dir()] if p.exists() else []
        return sorted((n for n in names if jalali.parse_folder(n)), key=lambda n: jalali.parse_folder(n))

    def _prune_sync(self, keep: int) -> list[str]:
        folders = self._list_date_folders_sync()
        doomed = folders[:-keep] if keep > 0 and len(folders) > keep else []
        for name in doomed:
            log.info("archive retention: deleting %s", name)
            if self.is_smb:
                self._smb().rmtree(unc_join(self._root_unc(), name))
            else:
                shutil.rmtree(Path(self.root) / name)
        return doomed

    async def prune(self) -> list[str]:
        """Keep only the newest N date folders. Only folders named like a Jalali
        date are ever touched."""
        keep = int(self.store.get_setting("archive_keep_days") or 14)
        return await asyncio.to_thread(self._prune_sync, keep)

    def _test_sync(self) -> str:
        probe = ".backuper-write-test"
        if self.is_smb:
            smb = self._smb()
            root = self._root_unc()
            smb.makedirs(root)
            smb.write_bytes(unc_join(root, probe), b"ok")
            smb.remove(unc_join(root, probe))
            free = smb.free_space(root)
        else:
            root_p = Path(self.root)
            root_p.mkdir(parents=True, exist_ok=True)
            (root_p / probe).write_bytes(b"ok")
            (root_p / probe).unlink()
            free = shutil.disk_usage(root_p).free
        folders = self._list_date_folders_sync()
        return (f"Archive {self.root} is writable. Free space: {human_size(free) if free is not None else '?'}. "
                f"{len(folders)} date folder(s).")

    async def test(self) -> str:
        if not self.enabled:
            return "Archive is disabled (no archive location set) — zips only go to Telegram."
        return await asyncio.to_thread(self._test_sync)
