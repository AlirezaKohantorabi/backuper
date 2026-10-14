r"""Thin synchronous wrapper around smbprotocol's smbclient (SMB2/3, pure Python).

Paths are UNC strings: \\server\share\dir\file. Call from a worker thread
(asyncio.to_thread) — every call does network I/O.

Every `SMB` object has its **own connection** per server (its own smbclient connection cache) and
should be closed when done (`close()` / `with`). smbclient's default is one shared connection per
server for the whole process; with several transfers to the same server at once that connection runs
out of SMB *credits* and smbprotocol fails instantly ("Request requires 1 credits but only 0 credits
are available") instead of waiting. `_install_credit_guard()` additionally makes requests wait for
credits, and makes sure a read is never sent with zero length (that looks like end-of-file).
"""

from __future__ import annotations

import logging
import ntpath
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import smbclient
import smbclient._io as _smb_io
from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBException
from smbprotocol.header import Commands

from backuper.fields import unc_server
from backuper.util import IncompleteCopy, check_complete, match_any  # noqa: F401

log = logging.getLogger(__name__)

COPY_CHUNK = 4 * 1024 * 1024
SMBError = (OSError, SMBException)
CREDIT_WAIT = 300  # seconds a request may wait for SMB credits before failing


def _credits(conn: Connection) -> int:
    with conn.sequence_lock:
        return conn.sequence_window["high"] - conn.sequence_window["low"]


def _install_credit_guard() -> None:
    """Patch smbprotocol so requests wait for credits instead of failing (idempotent).

    - `Connection._send`: waits until the whole request (all messages of a compound) fits in the
      credit window. Without this, a compound that fails half-way also leaks the credits of its
      first messages for the rest of the connection's life.
    - `smbclient._io._chunk_size`: with 0 credits it returns a 0-byte chunk; a 0-byte read then
      looks like end-of-file (a silently truncated copy). Wait for at least one credit instead.
    """
    if getattr(Connection._send, "_backuper_guard", False):  # noqa: SLF001
        return
    orig_send = Connection._send  # noqa: SLF001
    orig_chunk = _smb_io._chunk_size  # noqa: SLF001
    gates_lock = threading.Lock()

    def gate(conn: Connection) -> threading.Lock:
        g = conn.__dict__.get("_backuper_gate")
        if g is None:
            with gates_lock:
                g = conn.__dict__.setdefault("_backuper_gate", threading.Lock())
        return g

    def send(self, messages, *args, **kwargs):
        # The receiver thread sends keep-alive echoes; it must never wait (it's what returns credits).
        if threading.current_thread() is getattr(self, "_t_worker", None) or kwargs.get("message_id"):
            return orig_send(self, messages, *args, **kwargs)
        need = sum(max(self._calculate_credit_charge(m), 1)  # noqa: SLF001
                   for m in messages if m.COMMAND != Commands.SMB2_CANCEL)
        deadline = time.monotonic() + CREDIT_WAIT
        while True:
            with gate(self):  # check + send atomically w.r.t. other senders on this connection
                if _credits(self) >= need:
                    return orig_send(self, messages, *args, **kwargs)
            if time.monotonic() > deadline:
                raise SMBException(f"no SMB credits for {CREDIT_WAIT}s (need {need}, have {_credits(self)})")
            time.sleep(0.005)

    def chunk_size(connection, length, operation):
        deadline = time.monotonic() + CREDIT_WAIT
        while True:
            size, request = orig_chunk(connection, length, operation)
            if size > 0 or length <= 0:
                return size, request
            if time.monotonic() > deadline:
                raise SMBException(f"no SMB credits for {CREDIT_WAIT}s to {operation} data")
            time.sleep(0.005)

    send._backuper_guard = True  # type: ignore[attr-defined]
    Connection._send = send  # noqa: SLF001
    _smb_io._chunk_size = chunk_size  # noqa: SLF001


_install_credit_guard()


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
        self._cache: dict = {}  # this object's own connections (see the module docstring)
        self._lock = threading.Lock()

    def _base_kw(self) -> dict:
        return {"username": self.username, "password": self.password, "port": self.port,
                "connection_timeout": self.timeout, "connection_cache": self._cache}

    def _kw(self, path: str) -> dict:
        """smbclient arguments for `path`, connecting first (once per server, thread-safe)."""
        self.connect(path)
        return self._base_kw()

    def close(self) -> None:
        """Close this object's connections. Safe to call any time; the next call reconnects."""
        if self._cache:
            smbclient.reset_connection_cache(fail_on_error=False, connection_cache=self._cache)
            self._cache.clear()

    def __enter__(self) -> SMB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self, path: str) -> None:
        # Two threads registering the first session at once can tear each other's session down.
        with self._lock:
            smbclient.register_session(unc_server(path), **self._base_kw())

    # -- listing --------------------------------------------------------------
    def list_files(self, root: str, *, recursive: bool = False, patterns: list[str] | None = None,
                   exclude: list[str] | None = None) -> list[RemoteFile]:
        self.connect(root)
        out: list[RemoteFile] = []
        stack = [(root, "")]
        while stack:
            path, rel = stack.pop()
            for entry in smbclient.scandir(path, **self._kw(path)):
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

    def list_entries(self, root: str) -> list[tuple[str, bool, float]]:
        """(name, is_directory, modified time) of everything directly in `root`."""
        out = []
        for e in smbclient.scandir(root, **self._kw(root)):
            if e.name in (".", ".."):
                continue
            info = e.smb_info
            out.append((e.name, bool(info.file_attributes & _ATTR_DIRECTORY), _ts(info.last_write_time)))
        return out

    def list_dirs(self, root: str) -> list[str]:
        self.connect(root)
        return [e.name for e in smbclient.scandir(root, **self._kw(root)) if e.is_dir() and e.name not in (".", "..")]

    def stat(self, path: str):
        return smbclient.stat(path, **self._kw(path))

    def is_dir(self, path: str) -> bool:
        return smbclient.path.isdir(path, **self._kw(path))

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except SMBError:
            return False

    # -- read / write -------------------------------------------------------------
    def open_read(self, path: str) -> BinaryIO:
        # share_access 'rw' lets us read files other programs have open for writing (if they allow it).
        return smbclient.open_file(path, mode="rb", share_access="rw", buffering=COPY_CHUNK, **self._kw(path))

    def opener(self, path: str) -> Callable[[], BinaryIO]:
        return lambda: self.open_read(path)

    def upload(self, local: Path, remote: str, progress: Callable[[int], None] | None = None) -> None:
        self.connect(remote)
        tmp = remote + ".uploading"
        done = 0
        with open(local, "rb") as src, smbclient.open_file(tmp, mode="wb", **self._kw(tmp)) as dst:
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
            smbclient.remove(tmp, **self._kw(tmp))
            raise OSError(f"size mismatch after copy to {remote} ({remote_size} != {local.stat().st_size})")
        smbclient.replace(tmp, remote, **self._kw(remote))

    def open_write(self, remote: str) -> BinaryIO:
        """Seekable writer (zip files seek back to patch headers)."""
        return smbclient.open_file(remote, mode="wb", buffering=COPY_CHUNK, **self._kw(remote))

    def replace(self, src: str, dst: str) -> None:
        smbclient.replace(src, dst, **self._kw(dst))

    def server_copy(self, src: str, dst: str) -> None:
        """Copy within one server (SMB copy-chunk): the data never crosses the network."""
        self.connect(src)
        smbclient.copyfile(src, dst, **self._kw(dst))

    def copy_stream(self, opener: Callable[[], BinaryIO], dst: str) -> int:
        """Copy a readable stream into `dst`; raises IncompleteCopy if the source ended early."""
        with opener() as src, self.open_write(dst) as out:
            done = 0
            while buf := src.read(COPY_CHUNK):
                out.write(buf)
                done += len(buf)
            check_complete(src, done, dst)
        return done

    def set_mtime(self, path: str, mtime: float) -> None:
        """Keep the original modification time on a copy (best effort)."""
        try:
            smbclient.utime(path, times=(mtime, mtime), **self._kw(path))
        except SMBError:
            pass

    def rename(self, src: str, dst: str) -> None:
        smbclient.rename(src, dst, **self._kw(dst))

    def write_bytes(self, remote: str, data: bytes) -> None:
        with smbclient.open_file(remote, mode="wb", **self._kw(remote)) as f:
            f.write(data)

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        with self.open_read(remote) as src, open(local, "wb") as dst:
            done = 0
            while buf := src.read(COPY_CHUNK):
                dst.write(buf)
                done += len(buf)
            check_complete(src, done, remote)

    def makedirs(self, path: str) -> None:
        self.connect(path)
        smbclient.makedirs(path, exist_ok=True, **self._kw(path))

    def remove(self, path: str) -> None:
        smbclient.remove(path, **self._kw(path))

    def rmtree(self, path: str) -> None:
        for dirpath, dirnames, filenames in smbclient.walk(path, topdown=False, **self._kw(path)):
            for f in filenames:
                smbclient.remove(join(dirpath, f), **self._kw(path))
            for d in dirnames:
                smbclient.rmdir(join(dirpath, d), **self._kw(path))
        smbclient.rmdir(path, **self._kw(path))

    def free_space(self, path: str) -> int | None:
        try:
            v = smbclient.stat_volume(path, **self._kw(path))
            return v.caller_available_size
        except SMBError:
            return None
