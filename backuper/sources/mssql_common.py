"""Shared logic for SQL Server .bak files on a share: grouping, freshness, retention."""

from __future__ import annotations

import asyncio
import fnmatch
import re
import time

from backuper.sources.base import RunContext, SourceError
from backuper.transport.smb import SMB, RemoteFile

_DATE_TOKEN = re.compile(r"[_\-. ]*(?:19|20|13|14)\d{2}[-_.]?\d{2}[-_.]?\d{2}")
_NOISE_SUFFIX = re.compile(r"(?:[_\-. ]+(?:backup|full|copy_only|bak|db))+$", re.IGNORECASE)


def infer_db_name(file_name: str) -> str | None:
    """Best-effort database name from common .bak naming schemes:

    Maintenance plan:  Sales_backup_2026_10_07_010001_1234567.bak -> Sales
    Backuper trigger:  Sales_1405-07-15_0300.bak                   -> Sales
    Ola Hallengren:    SRV_Sales_FULL_20261007_010000.bak          -> SRV_Sales
    """
    stem = file_name.rsplit(".", 1)[0]
    m = _DATE_TOKEN.search(stem)
    if not m or m.start() == 0:
        return None
    prefix = _NOISE_SUFFIX.sub("", stem[: m.start()]).strip("_-. ")
    return prefix or None


GROUP_MODES = ("pattern", "database", "folder")


def _matches(f: RemoteFile, pattern: str) -> bool:
    """Patterns with a '/' match the path below the share (e.g. ArpaEn/*.bak), others the file name."""
    target = f.rel.replace("\\", "/") if "/" in pattern else f.name
    return fnmatch.fnmatchcase(target.lower(), pattern.lower())


def group_files(files: list[RemoteFile], patterns: list[str], group_by: str = "pattern") -> dict[str, list[RemoteFile]]:
    """Group candidate files. Each group yields one backup (its newest file)
    and has its own retention ("keep last N" counts per group).

    group_by: pattern  — one group per pattern
              database — by the database name in the file name (falls back to the sub-folder)
              folder   — by the sub-folder the file is in (e.g. Arpa\\, ArpaEn\\)
    """
    groups: dict[str, list[RemoteFile]] = {}
    for f in files:
        pattern = next((p for p in patterns if _matches(f, p)), None)
        if pattern is None:
            continue
        parent = f.rel.split("\\")[-2] if "\\" in f.rel else None
        if group_by == "database":
            key = infer_db_name(f.name) or parent or pattern
        elif group_by == "folder":
            key = parent or pattern
        else:
            key = pattern
        groups.setdefault(key, []).append(f)
    for v in groups.values():
        v.sort(key=lambda x: x.mtime, reverse=True)
    return groups


def label_for(group_key: str, newest: RemoteFile) -> str:
    """Readable item name for a group: 'Holoo1_*.zip' -> Holoo1, 'ArpaEn/*.bak' -> ArpaEn."""
    if not any(ch in group_key for ch in "*?["):
        return group_key
    if "/" in group_key and not any(ch in group_key.split("/")[0] for ch in "*?["):
        return group_key.split("/")[0]
    prefix = re.split(r"[*?\[]", group_key, maxsplit=1)[0].strip("_-. ")
    return prefix or infer_db_name(newest.name) or newest.name.rsplit(".", 1)[0]


def signature(f: RemoteFile) -> str:
    return f"{f.name}|{f.size}|{int(f.mtime)}"


def check_fresh(f: RemoteFile, max_age_hours: int) -> None:
    if max_age_hours <= 0:
        return
    age_h = (time.time() - f.mtime) / 3600
    if age_h > max_age_hours:
        raise SourceError(
            f"newest backup {f.name} is {age_h:.0f}h old (limit {max_age_hours}h) — "
            "is the SQL backup job failing?"
        )


async def wait_until_stable(smb: SMB, f: RemoteFile, ctx: RunContext, max_wait_s: int = 3600) -> RemoteFile:
    """Don't grab a .bak that SQL Server is still writing."""
    deadline = time.monotonic() + max_wait_s
    last = f.size
    while True:
        await asyncio.sleep(15 if time.time() - f.mtime < 300 else 0)
        try:
            # stat opens a handle, so the size is current even for a file being written
            st = await asyncio.to_thread(smb.stat, f.path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = -1, time.time()  # locked by the writer => still in progress
        if size == last and time.time() - mtime > 60:
            return RemoteFile(path=f.path, rel=f.rel, size=size, mtime=mtime)
        if time.monotonic() > deadline:
            raise SourceError(f"{f.name} is still being written after {max_wait_s // 60} min")
        ctx.log.info("%s still being written (%d bytes), waiting…", f.name, size)
        last = size
        await asyncio.sleep(30)


async def apply_retention(smb: SMB, files: list[RemoteFile], keep_last: int, ctx: RunContext) -> int:
    """Delete all but the newest `keep_last` files (list must be newest-first)."""
    if keep_last <= 0 or len(files) <= keep_last:
        return 0
    deleted = 0
    ctx.log.info("retention: %d file(s), keeping the newest %d (%s … %s)", len(files), keep_last,
                 files[0].name, files[keep_last - 1].name)
    for old in files[keep_last:]:
        try:
            await asyncio.to_thread(smb.remove, old.path)
            deleted += 1
            ctx.log.info("retention: deleted %s (%s)", old.name,
                         time.strftime("%Y-%m-%d %H:%M", time.localtime(old.mtime)))
        except OSError as e:
            ctx.warn(f"couldn't delete old backup {old.name}: {e}")
    return deleted
