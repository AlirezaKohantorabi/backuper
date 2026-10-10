"""One small interface over FTP, FTPS and SFTP for pulling files off remote hosts."""

from __future__ import annotations

import asyncio
import ftplib
import logging
import posixpath
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import asyncssh

from backuper.transport import ssh

log = logging.getLogger(__name__)

PROTOCOLS = ("ftps", "ftp", "sftp")
DEFAULT_PORTS = {"ftp": 21, "ftps": 21, "sftp": 22}


@dataclass
class RemoteEntry:
    name: str
    path: str
    size: int
    is_dir: bool
    mtime: float = 0.0


class RemoteFS(ABC):
    @abstractmethod
    async def __aenter__(self) -> RemoteFS: ...

    @abstractmethod
    async def __aexit__(self, *exc) -> None: ...

    @abstractmethod
    async def listdir(self, path: str) -> list[RemoteEntry]: ...

    @abstractmethod
    async def download(self, path: str, local: Path) -> None: ...

    @abstractmethod
    async def remove(self, path: str) -> None: ...

    async def walk_files(self, root: str) -> list[RemoteEntry]:
        out: list[RemoteEntry] = []
        stack = [root]
        while stack:
            d = stack.pop()
            for e in await self.listdir(d):
                if e.is_dir:
                    stack.append(e.path)
                else:
                    out.append(e)
        return out


# -- FTP / FTPS ---------------------------------------------------------------------

class _FTPS(ftplib.FTP_TLS):
    """FTP_TLS that reuses the control connection's TLS session for data
    connections — required by vsftpd/pure-ftpd setups common on cPanel/DirectAdmin."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class FTPFS(RemoteFS):
    def __init__(self, host: str, port: int, username: str, password: str, *, tls: bool, verify_tls: bool = True,
                 timeout: int = 120):
        self.host, self.port, self.username, self.password = host, port, username, password
        self.tls, self.verify_tls, self.timeout = tls, verify_tls, timeout
        self._ftp: ftplib.FTP | None = None
        self._lock = asyncio.Lock()

    def _connect(self) -> ftplib.FTP:
        if self.tls:
            ctx = ssl.create_default_context()
            if not self.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp: ftplib.FTP = _FTPS(context=ctx, timeout=self.timeout)
        else:
            ftp = ftplib.FTP(timeout=self.timeout)
        ftp.connect(self.host, self.port)
        ftp.login(self.username, self.password)
        if self.tls:
            ftp.prot_p()  # type: ignore[attr-defined]
        ftp.set_pasv(True)
        return ftp

    async def __aenter__(self) -> FTPFS:
        self._ftp = await asyncio.to_thread(self._connect)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ftp:
            ftp = self._ftp
            self._ftp = None
            try:
                await asyncio.to_thread(ftp.quit)
            except Exception:  # noqa: BLE001
                ftp.close()

    def _listdir_sync(self, path: str) -> list[RemoteEntry]:
        assert self._ftp
        out: list[RemoteEntry] = []
        try:
            for name, facts in self._ftp.mlsd(path, facts=["type", "size", "modify"]):
                if name in (".", "..") or facts.get("type") in ("cdir", "pdir"):
                    continue
                mtime = 0.0
                if "modify" in facts:
                    try:
                        mtime = datetime.strptime(facts["modify"][:14], "%Y%m%d%H%M%S").replace(
                            tzinfo=timezone.utc).timestamp()
                    except ValueError:
                        pass
                out.append(RemoteEntry(name=name, path=posixpath.join(path, name),
                                       size=int(facts.get("size", 0) or 0), is_dir=facts.get("type") == "dir",
                                       mtime=mtime))
            return out
        except ftplib.error_perm:
            pass  # server without MLSD: fall back to NLST + SIZE
        for full in self._ftp.nlst(path):
            name = posixpath.basename(full.rstrip("/"))
            if name in (".", ".."):
                continue
            p = posixpath.join(path, name)
            try:
                self._ftp.voidcmd("TYPE I")
                size = self._ftp.size(p) or 0
                out.append(RemoteEntry(name=name, path=p, size=size, is_dir=False))
            except ftplib.error_perm:
                out.append(RemoteEntry(name=name, path=p, size=0, is_dir=True))
        return out

    async def listdir(self, path: str) -> list[RemoteEntry]:
        async with self._lock:
            return await asyncio.to_thread(self._listdir_sync, path)

    def _download_sync(self, path: str, local: Path) -> None:
        assert self._ftp
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as f:
            self._ftp.retrbinary(f"RETR {path}", f.write, blocksize=1024 * 1024)

    async def download(self, path: str, local: Path) -> None:
        async with self._lock:
            await asyncio.to_thread(self._download_sync, path, local)

    async def remove(self, path: str) -> None:
        async with self._lock:
            assert self._ftp
            await asyncio.to_thread(self._ftp.delete, path)


# -- SFTP ---------------------------------------------------------------------------------

class SFTPFS(RemoteFS):
    def __init__(self, target: ssh.SSHTarget):
        self.target = target
        self.host_key = ""
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None

    async def __aenter__(self) -> SFTPFS:
        self._conn, self.host_key = await ssh.connect(self.target)
        self._sftp = await self._conn.start_sftp_client()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._sftp:
            self._sftp.exit()
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()

    async def listdir(self, path: str) -> list[RemoteEntry]:
        assert self._sftp
        out = []
        for e in await self._sftp.readdir(path or "."):
            if e.filename in (".", ".."):
                continue
            a = e.attrs
            is_dir = a.type == asyncssh.FILEXFER_TYPE_DIRECTORY
            out.append(RemoteEntry(name=e.filename, path=posixpath.join(path or ".", e.filename),
                                   size=a.size or 0, is_dir=is_dir, mtime=float(a.mtime or 0)))
        return out

    async def download(self, path: str, local: Path) -> None:
        assert self._sftp
        local.parent.mkdir(parents=True, exist_ok=True)
        await self._sftp.get(path, str(local), block_size=256 * 1024, max_requests=32)

    async def remove(self, path: str) -> None:
        assert self._sftp
        await self._sftp.remove(path)


def open_remote(protocol: str, host: str, port: int, username: str, password: str, *,
                verify_tls: bool = True, pinned_key: str = "") -> RemoteFS:
    if protocol == "sftp":
        return SFTPFS(ssh.SSHTarget(host=host, port=port, username=username, password=password,
                                    pinned_key=pinned_key))
    if protocol in ("ftp", "ftps"):
        return FTPFS(host, port, username, password, tls=protocol == "ftps", verify_tls=verify_tls)
    raise ValueError(f"Unknown transfer protocol {protocol!r}")
