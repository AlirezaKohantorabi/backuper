r"""Thin synchronous wrapper around smbprotocol's smbclient (SMB2/3, pure Python).

Paths are UNC strings: \\server\share\dir\file. Call from a worker thread
(asyncio.to_thread) — every call does network I/O.
"""

from __future__ import annotations

import logging
import ntpath
import shutil
import stat as statmod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import smbclient
from smbprotocol.exceptions import SMBException

from backuper.fields import unc_server
from backuper.util import match_any

log = logging.getLogger(__name__)

COPY_CHUNK = 4 * 1024 * 1024
SMBError = (OSError, SMBException)


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
                st = entry.stat()
                if statmod.S_ISDIR(st.st_mode):
                    if recursive:
                        stack.append((entry.path, entry_rel))
                    continue
                if patterns and not (match_any(name, patterns) or match_any(entry_rel.replace("\\", "/"), patterns)):
                    continue
                out.append(RemoteFile(path=entry.path, rel=entry_rel, size=st.st_size, mtime=st.st_mtime))
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
