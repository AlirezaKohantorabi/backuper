"""Shared bits for hosting-panel sources (cPanel, DirectAdmin): the file-transfer
leg that pulls the finished archive off the host and deletes it there."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
import time

from backuper.fields import BOOL, CHOICE, CREDENTIAL, INT, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import RunContext, SourceError
from backuper.transport.remote import DEFAULT_PORTS, PROTOCOLS, RemoteEntry, RemoteFS, open_remote

ARCHIVE_PATTERNS = ["backup-*.tar.gz", "backup-*.tar.zst", "backup-*.tar", "backup-*.tgz"]

TRANSFER_FIELDS = [
    Field("transfer", "Download via", CHOICE, default="ftps", choices=PROTOCOLS,
          help="How to pull the finished archive. ftps = FTP over TLS (recommended over plain ftp)."),
    Field("transfer_port", "Download port", INT, default=0, min=0, max=65535, help="0 = default (21 / 22)."),
    Field("transfer_credential", "Download login", CREDENTIAL, required=False, default="",
          help="FTP/SFTP login. Empty = same as the panel login (only works with password auth)."),
    Field("verify_tls", "Verify TLS certificates", BOOL, default=True,
          help="Turn off only for hosts with self-signed certificates."),
    Field("timeout_minutes", "Timeout (minutes)", INT, default=180, min=5, max=24 * 60),
]


def open_transfer(ctx: RunContext, cfg: dict, panel_cred_name: str) -> RemoteFS:
    cred = ctx.credential(cfg.get("transfer_credential") or panel_cred_name, what="download login")
    proto = cfg.get("transfer", "ftps")
    port = int(cfg.get("transfer_port") or DEFAULT_PORTS[proto])
    return open_remote(proto, cfg["host"], port, cred.username, cred.password,
                       verify_tls=bool(cfg.get("verify_tls", True)),
                       pinned_key=ctx.get_state("transfer_host_key", ""))


def pin_transfer_key(ctx: RunContext, fs: RemoteFS) -> None:
    key = getattr(fs, "host_key", "")
    if key and not ctx.get_state("transfer_host_key"):
        ctx.set_state("transfer_host_key", key)


def is_archive(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in ARCHIVE_PATTERNS)


async def list_archives(fs: RemoteFS, remote_dir: str) -> dict[str, RemoteEntry]:
    return {e.name: e for e in await fs.listdir(remote_dir) if not e.is_dir and is_archive(e.name)}


async def wait_new_archive_stable(fs: RemoteFS, remote_dir: str, before: set[str], ctx: RunContext,
                                  timeout_s: int, poll_s: int = 20, stable_polls: int = 3) -> RemoteEntry:
    """Wait for a new backup-* archive to appear and stop growing."""
    deadline = time.monotonic() + timeout_s
    last_size, same = -1, 0
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        new = [e for n, e in (await list_archives(fs, remote_dir)).items() if n not in before]
        if not new:
            continue
        cur = max(new, key=lambda e: (e.mtime, e.name))
        if cur.size > 0 and cur.size == last_size:
            same += 1
            if same >= stable_polls:
                return cur
        else:
            same = 0
        last_size = cur.size
        ctx.log.info("waiting for %s to finish (%d bytes)", cur.name, cur.size)
    raise SourceError(f"the panel didn't produce a finished backup within {timeout_s // 60} min")


async def download_and_delete(fs: RemoteFS, remote_dir: str, name: str, ctx: RunContext) -> Artifact:
    remote = posixpath.join(remote_dir, name)
    local = ctx.workdir / name
    ctx.log.info("downloading %s", remote)
    try:
        await fs.download(remote, local)
    finally:
        # The archive must never stay on the hosting account, even if the download failed.
        try:
            await fs.remove(remote)
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"couldn't delete {remote} from the host — delete it manually: {e}")
    return Artifact(label="", entries=[Entry.from_path(local, compress=False)], cleanup=[local])
