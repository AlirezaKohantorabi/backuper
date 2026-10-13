"""Shared bits for hosting-panel sources (cPanel, DirectAdmin): the file-transfer
leg that pulls the finished archive off the host and deletes it there."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
import time

from backuper.fields import BOOL, CHOICE, CREDENTIAL, INT, TEXT, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import RunContext, SourceError
from backuper.transport.remote import DEFAULT_PORTS, PROTOCOLS, RemoteEntry, RemoteFS, open_remote

ARCHIVE_PATTERNS = ["backup-*.tar.gz", "backup-*.tar.zst", "backup-*.tar", "backup-*.tgz"]

KEEP_MODES = ("no", "newest", "replace")


def keep_mode(cfg: dict) -> str:
    """'no' | 'newest' | 'replace' (old configs stored a yes/no: yes = newest)."""
    v = cfg.get("keep_on_host")
    if v is True:
        return "newest"
    return v if v in KEEP_MODES else "no"


TRANSFER_FIELDS = [
    Field("transfer", "Download via", CHOICE, default="ftps", choices=PROTOCOLS,
          help="How to pull the finished archive. ftps = FTP over TLS (recommended over plain ftp)."),
    Field("transfer_host", "Download host", TEXT, required=False, default="",
          help="Empty = same as the panel host. On shared hosting the FTP certificate is often issued to the "
               "server's own name (e.g. box402.parsvds.com) — put that here to keep certificate checks on."),
    Field("transfer_port", "Download port", INT, default=0, min=0, max=65535, help="0 = default (21 / 22)."),
    Field("transfer_credential", "Download login", CREDENTIAL, required=False, default="",
          help="FTP/SFTP login. Empty = same as the panel login (only works with password auth)."),
    Field("verify_tls", "Verify TLS certificates", BOOL, default=True,
          help="Turn off only for hosts with self-signed certificates."),
    Field("keep_on_host", "Backup copy on the host", CHOICE, default="no", choices=KEEP_MODES,
          help="no: nothing stays on the hosting account. newest: this run's backup stays as an extra copy, older "
               "ones are deleted after the download (needs room for two backups). replace: older backups are "
               "deleted BEFORE the new one is made, and the new one stays (needs room for one)."),
    Field("timeout_minutes", "Timeout (minutes)", INT, default=180, min=5, max=24 * 60),
]


def _log_download(ctx: RunContext, remote: str, local, t0: float) -> None:
    size, secs = local.stat().st_size, max(time.monotonic() - t0, 0.001)
    ctx.log.info("downloaded %s: %.1f MB in %.0fs (%.1f MB/s)", remote, size / 2**20, secs, size / 2**20 / secs)


def open_transfer(ctx: RunContext, cfg: dict, panel_cred_name: str) -> RemoteFS:
    cred = ctx.credential(cfg.get("transfer_credential") or panel_cred_name, what="download login")
    proto = cfg.get("transfer", "ftps")
    port = int(cfg.get("transfer_port") or DEFAULT_PORTS[proto])
    return open_remote(proto, cfg.get("transfer_host") or cfg["host"], port, cred.username, cred.password,
                       verify_tls=bool(cfg.get("verify_tls", True)),
                       pinned_key=ctx.get_state("transfer_host_key", ""))


def pin_transfer_key(ctx: RunContext, fs: RemoteFS) -> None:
    key = getattr(fs, "host_key", "")
    if key and not ctx.get_state("transfer_host_key"):
        ctx.set_state("transfer_host_key", key)


def is_archive(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in ARCHIVE_PATTERNS)


def companions(name: str) -> list[str]:
    """Side files a panel writes next to a backup (DirectAdmin: backup-….json)."""
    stem = name
    for ext in (".tar.gz", ".tar.zst", ".tgz", ".tar"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return [stem + ".json"]


async def remove_archive(fs: RemoteFS, remote_dir: str, name: str, ctx: RunContext) -> None:
    """Delete a backup archive and its side files from the host (side files may not exist)."""
    await fs.remove(posixpath.join(remote_dir, name))
    ctx.log.info("deleted %s from the host", name)
    for extra in companions(name):
        try:
            await fs.remove(posixpath.join(remote_dir, extra))
            ctx.log.debug("deleted %s from the host", extra)
        except Exception:  # noqa: BLE001 - usually just not there
            pass


async def clear_old_archives(fs: RemoteFS, remote_dir: str, ctx: RunContext) -> int:
    """'replace' mode: remove existing backups BEFORE making a new one (quota needs room for one only)."""
    n = 0
    for name in list(await list_archives(fs, remote_dir)):
        await remove_archive(fs, remote_dir, name, ctx)
        ctx.note(f"deleted older backup {name} from the host before making a new one")
        n += 1
    return n


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


async def download_and_delete(fs: RemoteFS, remote_dir: str, name: str, ctx: RunContext,
                              keep_on_host: bool = False) -> Artifact:
    """Download the new archive. Then either delete it from the host (default), or keep it as the
    single copy on the host and delete every older backup archive there."""
    remote = posixpath.join(remote_dir, name)
    local = ctx.workdir / name
    ctx.log.info("downloading %s", remote)
    t0 = time.monotonic()
    if keep_on_host:
        await fs.download(remote, local)
        _log_download(ctx, remote, local, t0)
        for old in await list_archives(fs, remote_dir):
            if old == name:
                continue
            try:
                await remove_archive(fs, remote_dir, old, ctx)
                ctx.note(f"deleted older backup {old} from the host")
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"couldn't delete older backup {old} from the host: {e}")
    else:
        try:
            await fs.download(remote, local)
            _log_download(ctx, remote, local, t0)
        finally:
            # Nothing may stay on the hosting account, even if the download failed.
            try:
                await remove_archive(fs, remote_dir, name, ctx)
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"couldn't delete {remote} from the host — delete it manually: {e}")
    return Artifact(label="", entries=[Entry.from_path(local, compress=False)], cleanup=[local])
