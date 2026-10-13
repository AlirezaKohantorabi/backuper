r"""Thin synchronous wrapper around smbprotocol's smbclient (SMB2/3, pure Python).

Paths are UNC strings: \\server\share\dir\file. Call from a worker thread
(asyncio.to_thread) — every call does network I/O.
"""

from __future__ import annotations

import logging
import ntpath
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import smbclient
from smbprotocol.exceptions import SMBException

from backuper.fields import unc_server
from backuper.util import match_any

log = logging.getLogger(__name__)

COPY_CHUNK = 4 * 1024 * 1024
SMBError = (OSError, SMBException)


_ATTR_DIRECTORY = 0x10
_ATTR_REPARSE = 0x400


def _ts(value) -> float:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    return float(value)


@dataclass
class RemoteFile:
    path: str      # full UNC path
    rel: str       # path relative to the listed root, '\' separated
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return ntpath.basename(self.path)


def join(*parts: str) -> str:
    return ntpath.join(*parts)


class SMB:
    def __init__(self, username: str, password: str, port: int = 445, timeout: int = 60):
        self.username = username or None
        self.password = password or None
        self.port = port
        self.timeout = timeout

    def _kw(self) -> dict:
        return {"username": self.username, "password": self.password, "port": self.port,
                "connection_timeout": self.timeout}

    def connect(self, path: str) -> None:
        smbclient.register_session(unc_server(path), **self._kw())

    # -- listing --------------------------------------------------------------
    def list_files(self, root: str, *, recursive: bool = False, patterns: list[str] | None = None,
                   exclude: list[str] | None = None) -> list[RemoteFile]:
        self.connect(root)
        out: list[RemoteFile] = []
        stack = [(root, "")]
        while stack:
            path, rel = stack.pop()
            for entry in smbclient.scandir(path, **self._kw()):
                name = entry.name
                if name in (".", ".."):
                    continue
                entry_rel = f"{rel}\\{name}" if rel else name
                if exclude and (match_any(name, exclude) or match_any(entry_rel.replace("\\", "/"), exclude)):
                    continue
                # Size/date come with the directory listing itself (entry.smb_info); entry.stat()
                # would cost one extra network round trip per file.
                info = entry.smb_info
                if info.file_attributes & _ATTR_DIRECTORY:
                    if recursive and not info.file_attributes & _ATTR_REPARSE:
                        stack.append((entry.path, entry_rel))
                    continue
                if patterns and not (match_any(name, patterns) or match_any(entry_rel.replace("\\", "/"), patterns)):
                    continue
                out.append(RemoteFile(path=entry.path, rel=entry_rel, size=info.end_of_file,
                                      mtime=_ts(info.last_write_time)))
        return out

    def list_dirs(self, root: str) -> list[str]:
        self.connect(root)
        return [e.name for e in smbclient.scandir(root, **self._kw()) if e.is_dir() and e.name not in (".", "..")]

    def stat(self, path: str):
        return smbclient.stat(path, **self._kw())

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except SMBError:
            return False

    # -- read / write -------------------------------------------------------------
    def open_read(self, path: str) -> BinaryIO:
        # share_access 'rw' lets us read files other programs have open for writing (if they allow it).
        return smbclient.open_file(path, mode="rb", share_access="rw", buffering=COPY_CHUNK, **self._kw())

    def opener(self, path: str) -> Callable[[], BinaryIO]:
        return lambda: self.open_read(path)

    def upload(self, local: Path, remote: str, progress: Callable[[int], None] | None = None) -> None:
        self.connect(remote)
        tmp = remote + ".uploading"
        done = 0
        with open(local, "rb") as src, smbclient.open_file(tmp, mode="wb", **self._kw()) as dst:
            while True:
                buf = src.read(COPY_CHUNK)
                if not buf:
                    break
                dst.write(buf)
                done += len(buf)
                if progress:
                    progress(done)
        remote_size = self.stat(tmp).st_size
        if remote_size != local.stat().st_size:
            smbclient.remove(tmp, **self._kw())
            raise OSError(f"size mismatch after copy to {remote} ({remote_size} != {local.stat().st_size})")
        smbclient.replace(tmp, remote, **self._kw())

    def open_write(self, remote: str) -> BinaryIO:
        """Seekable writer (zip files seek back to patch headers)."""
        return smbclient.open_file(remote, mode="wb", buffering=COPY_CHUNK, **self._kw())

    def replace(self, src: str, dst: str) -> None:
        smbclient.replace(src, dst, **self._kw())

    def server_copy(self, src: str, dst: str) -> None:
        """Copy within one server (SMB copy-chunk): the data never crosses the network."""
        smbclient.copyfile(src, dst, **self._kw())

    def copy_stream(self, opener: Callable[[], BinaryIO], dst: str) -> None:
        with opener() as src, self.open_write(dst) as out:
            shutil.copyfileobj(src, out, COPY_CHUNK)

    def set_mtime(self, path: str, mtime: float) -> None:
        """Keep the original modification time on a copy (best effort)."""
        try:
            smbclient.utime(path, times=(mtime, mtime), **self._kw())
        except SMBError:
            pass

    def rename(self, src: str, dst: str) -> None:
        smbclient.rename(src, dst, **self._kw())

    def write_bytes(self, remote: str, data: bytes) -> None:
        with smbclient.open_file(remote, mode="wb", **self._kw()) as f:
            f.write(data)

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        with self.open_read(remote) as src, open(local, "wb") as dst:
            shutil.copyfileobj(src, dst, COPY_CHUNK)

    def makedirs(self, path: str) -> None:
        self.connect(path)
        smbclient.makedirs(path, exist_ok=True, **self._kw())

    def remove(self, path: str) -> None:
        smbclient.remove(path, **self._kw())

    def rmtree(self, path: str) -> None:
        for dirpath, dirnames, filenames in smbclient.walk(path, topdown=False, **self._kw()):
            for f in filenames:
                smbclient.remove(join(dirpath, f), **self._kw())
            for d in dirnames:
                smbclient.rmdir(join(dirpath, d), **self._kw())
        smbclient.rmdir(path, **self._kw())

    def free_space(self, path: str) -> int | None:
        try:
            v = smbclient.stat_volume(path, **self._kw())
            return v.caller_available_size
        except SMBError:
            return None
