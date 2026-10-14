r"""The long-term archive: <archive_path>\<jalali date>\<zip files>.

`archive_path` is either a network share (\\server\share\folder, written over
SMB) or a local directory on this server.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import ntpath
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TypeVar

from backuper import jalali
from backuper.db import Store
from backuper.fields import unc_server
from backuper.models import Artifact
from backuper.transport.smb import SMB, SMBError
from backuper.transport.smb import join as unc_join
from backuper.util import check_complete, human_size

log = logging.getLogger(__name__)
T = TypeVar("T")
LEFTOVER_SUFFIXES = (".uploading", ".partial")
COPY_WORKERS = 4  # parallel file copies (each with its own connection) for plain folder copies
LEFTOVER_MIN_AGE = 15 * 60  # seconds; younger leftovers may belong to a copy still running elsewhere


class ArchiveError(RuntimeError):
    pass


def _is_unreadable(ex: BaseException) -> bool:
    """A source file we may skip (locked, access denied, vanished) — as opposed to a connection or
    archive problem, which must fail the copy so it can be retried."""
    text = str(ex)
    return any(k in text for k in ("STATUS_ACCESS_DENIED", "STATUS_SHARING_VIOLATION", "STATUS_OBJECT_NAME_NOT_FOUND",
                                   "STATUS_OBJECT_PATH_NOT_FOUND", "STATUS_DELETE_PENDING", "STATUS_FILE_IS_A_DIRECTORY",
                                   "STATUS_LOCK_NOT_GRANTED", "STATUS_FILE_LOCK_CONFLICT", "IncompleteCopy",
                                   "incomplete copy")) or isinstance(ex, PermissionError | FileNotFoundError)


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
        """A new SMB client with its own connection — use it with `with` so it's closed afterwards."""
        name = self.store.get_setting("archive_credential") or ""
        cred = self.store.get_credential_by_name(name) if name else None
        if name and cred is None:
            raise ArchiveError(f"archive credential '{name}' doesn't exist")
        return SMB(cred.username if cred else "", cred.password if cred else "")

    def _root_unc(self) -> str:
        return self.root.replace("/", "\\")

    def _discard(self, path: str, *, tree: bool = False) -> None:
        """Best-effort removal of a half-written file/folder after a failure, over a FRESH connection
        (the one that just failed may be unusable). Logged, never raises."""
        for attempt in range(3):
            try:
                with self._smb() as smb:
                    if not smb.exists(path):
                        return
                    smb.rmtree(path) if tree else smb.remove(path)
                log.info("removed unfinished %s", path)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("couldn't remove unfinished %s (attempt %d): %s", path, attempt + 1, e)
                time.sleep(5)

    # -- operations (run in threads) ---------------------------------------------
    def _store_sync(self, local: Path, folder: str, name: str, progress: Callable[[int], None] | None) -> str:
        rel = f"{folder}/{name}"
        if self.is_smb:
            d = unc_join(self._root_unc(), folder)
            try:
                with self._smb() as smb:
                    smb.makedirs(d)
                    smb.upload(local, unc_join(d, name), progress)
            except BaseException:
                self._discard(unc_join(d, name + ".uploading"))
                raise
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
            d = unc_join(self._root_unc(), folder)
            final = unc_join(d, name)
            tmp = final + ".uploading"
            try:
                with self._smb() as smb:
                    smb.makedirs(d)
                    with smb.open_write(tmp) as f:
                        result = write(f)
                    size = smb.stat(tmp).st_size
                    smb.replace(tmp, final)
            except BaseException:
                self._discard(tmp)
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
        """Delete a zip or a copied folder. False if it's already gone (e.g. its date folder was pruned)."""
        folder, name = rel.split("/", 1)
        try:
            if self.is_smb:
                path = unc_join(self._root_unc(), folder, name)
                with self._smb() as smb:
                    if smb.is_dir(path):
                        smb.rmtree(path)
                    else:
                        smb.remove(path)
            else:
                p = Path(self.root) / folder / name
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            return True
        except (FileNotFoundError, SMBError):
            return False

    def _copy_tree_sync(self, art: Artifact, folder: str, dirname: str, skip_unreadable: bool,
                        progress: Callable[[int], None] | None) -> tuple[str, int, list[str]]:
        """Copy every entry as a plain file into <folder>/<dirname>/ (written as <dirname>.partial
        and renamed at the end, so a half-finished copy is never mistaken for a backup).

        Resumable: if <dirname>.partial is already there (an earlier attempt of this copy failed),
        files in it with the same size and date are kept and only the rest is copied. The .partial
        folder is left in place on failure so the engine's retry can resume; the engine discards it
        when it gives up (`discard_copy`)."""
        warnings: list[str] = []
        done = 0
        if self.is_smb:
            final = unc_join(self._root_unc(), folder, dirname)
            tmp = final + ".partial"
            host = unc_server(self._root_unc()).lower()
            with self._smb() as smb:
                have: dict[str, tuple[int, float]] = {}
                if smb.exists(tmp):
                    have = {f.rel.lower(): (f.size, f.mtime) for f in smb.list_files(tmp, recursive=True)}
                    log.info("resuming %s: %d file(s) already copied", tmp, len(have))
                todo = []
                for e in art.entries:
                    old = have.get(e.arcname.replace("/", "\\").lower())
                    if old and old[0] == e.size and abs(old[1] - e.mtime) < 2:
                        done += e.size  # copied by an earlier attempt
                    else:
                        todo.append(e)
                # Folders first (parents before children), so the workers only copy files.
                smb.makedirs(tmp)
                for d in sorted({ntpath.dirname(unc_join(tmp, *e.arcname.split("/"))) for e in todo},
                                key=lambda x: (x.count("\\"), x.lower())):
                    if d.lower() != tmp.lower():
                        smb.makedirs(d)
            done = self._copy_files_parallel(todo, tmp, host, skip_unreadable, progress, done, warnings)
            with self._smb() as smb:
                if smb.exists(final):
                    smb.rmtree(final)
                smb.rename(tmp, final)
        else:
            final_p = Path(self.root) / folder / dirname
            tmp_p = final_p.with_name(dirname + ".partial")
            have = {}
            if tmp_p.exists():
                have = {str(f.relative_to(tmp_p)).replace("/", "\\").lower(): (f.stat().st_size, f.stat().st_mtime)
                        for f in tmp_p.rglob("*") if f.is_file()}
                log.info("resuming %s: %d file(s) already copied", tmp_p, len(have))
            for e in art.entries:
                old = have.get(e.arcname.replace("/", "\\").lower())
                if old and old[0] == e.size and abs(old[1] - e.mtime) < 2:
                    done += e.size  # copied by an earlier attempt
                    continue
                dst_p = tmp_p.joinpath(*e.arcname.split("/"))
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with e.opener() as src, open(dst_p, "wb") as dst:
                        n = 0
                        while buf := src.read(4 * 1024 * 1024):
                            dst.write(buf)
                            n += len(buf)
                        check_complete(src, n, e.arcname)
                    os.utime(dst_p, (e.mtime, e.mtime))
                except OSError as ex:
                    if not skip_unreadable or not _is_unreadable(ex):
                        raise
                    dst_p.unlink(missing_ok=True)
                    warnings.append(f"skipped {e.arcname}: {ex.__class__.__name__} {ex}")
                    continue
                done += e.size
                if progress:
                    progress(done)
            shutil.rmtree(final_p, ignore_errors=True)
            os.replace(tmp_p, final_p)
        return f"{folder}/{dirname}", done, warnings

    def _copy_files_parallel(self, todo: list, tmp: str, host: str, skip_unreadable: bool,
                             progress: Callable[[int], None] | None, done: int, warnings: list[str]) -> int:
        """Copy `todo` entries into the folder `tmp` with COPY_WORKERS threads, each on its own connection
        (copying is mostly per-file round trips, so parallel workers are several times faster).
        The first real error stops all workers and is raised. Returns the new byte count."""
        lock = threading.Lock()
        stop = threading.Event()
        errors: list[BaseException] = []
        queue = iter(todo)
        total = [done]

        def worker() -> None:
            with self._smb() as smb:
                while not stop.is_set():
                    with lock:
                        e = next(queue, None)
                    if e is None:
                        return
                    dst = unc_join(tmp, *e.arcname.split("/"))
                    try:
                        if e.source_path and unc_server(e.source_path).lower() == host:
                            try:
                                smb.server_copy(e.source_path, dst)  # data stays inside that server
                            except SMBError as ex:
                                log.debug("server-side copy of %s failed (%s), streaming instead", e.arcname, ex)
                                smb.copy_stream(e.opener, dst)
                        else:
                            smb.copy_stream(e.opener, dst)
                        smb.set_mtime(dst, e.mtime)
                    except SMBError as ex:
                        if not skip_unreadable or not _is_unreadable(ex):
                            errors.append(ex)
                            stop.set()
                            return
                        try:
                            if smb.exists(dst):
                                smb.remove(dst)  # don't leave a half-copied file behind
                        except SMBError:
                            pass
                        warnings.append(f"skipped {e.arcname}: {ex.__class__.__name__} {ex}")
                        continue
                    except BaseException as ex:  # noqa: BLE001 - handed to the caller below
                        errors.append(ex)
                        stop.set()
                        return
                    with lock:
                        total[0] += e.size
                        if progress:
                            progress(total[0])

        # Each thread gets a copy of the caller's context, so log lines keep their [run#N source] tag.
        threads = [threading.Thread(target=contextvars.copy_context().run, args=(worker,), name=f"copy-{i}",
                                    daemon=True) for i in range(min(COPY_WORKERS, max(1, len(todo))))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        return total[0]

    async def discard_copy(self, folder: str, dirname: str) -> None:
        """Remove the unfinished <dirname>.partial of a copy that finally failed."""
        if self.is_smb:
            await asyncio.to_thread(self._discard, unc_join(self._root_unc(), folder, dirname + ".partial"), tree=True)
        else:
            shutil.rmtree(Path(self.root) / folder / (dirname + ".partial"), ignore_errors=True)

    async def copy_tree(self, art: Artifact, folder: str, dirname: str, *, skip_unreadable: bool = True,
                        progress: Callable[[int], None] | None = None) -> tuple[str, int, list[str]]:
        """Store an artifact as a plain folder instead of a zip. Returns (relative path, bytes copied, warnings)."""
        return await asyncio.to_thread(self._copy_tree_sync, art, folder, dirname, skip_unreadable, progress)

    async def delete(self, rel: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, rel)

    async def store_file(self, local: Path, folder: str, name: str,
                         progress: Callable[[int], None] | None = None) -> str:
        """Copy a finished zip into the archive. Returns its relative path."""
        return await asyncio.to_thread(self._store_sync, local, folder, name, progress)

    def write_status(self, name: str, text: str) -> None:
        """Write a small text file in the archive root (the live status file). Blocking; no-op without archive."""
        if not self.enabled:
            return
        data = ("\ufeff" + text).encode("utf-8")  # BOM: Notepad shows Persian names correctly
        if self.is_smb:
            with self._smb() as smb:
                smb.write_bytes(unc_join(self._root_unc(), name), data)
        else:
            root = Path(self.root)
            root.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)

    def _fetch_sync(self, rel: str, local: Path) -> None:
        folder, name = rel.split("/", 1)
        local.parent.mkdir(parents=True, exist_ok=True)
        if self.is_smb:
            with self._smb() as smb:
                smb.download(unc_join(self._root_unc(), folder, name), local)
        else:
            shutil.copyfile(Path(self.root) / folder / name, local)

    async def fetch(self, rel: str, local: Path) -> None:
        await asyncio.to_thread(self._fetch_sync, rel, local)

    def _list_date_folders_sync(self) -> list[str]:
        if self.is_smb:
            with self._smb() as smb:
                names = smb.list_dirs(self._root_unc())
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
                with self._smb() as smb:
                    smb.rmtree(unc_join(self._root_unc(), name))
            else:
                shutil.rmtree(Path(self.root) / name)
        return doomed

    def _clean_leftovers_sync(self) -> list[str]:
        """Remove *.uploading files and *.partial folders left in date folders by a run that was
        interrupted (service restart, crash). Skips anything modified in the last 15 minutes."""
        removed: list[str] = []
        cutoff = time.time() - LEFTOVER_MIN_AGE
        for folder in self._list_date_folders_sync():
            if self.is_smb:
                d = unc_join(self._root_unc(), folder)
                with self._smb() as smb:
                    for n, is_dir, mtime in smb.list_entries(d):
                        if not n.endswith(LEFTOVER_SUFFIXES) or mtime > cutoff:
                            continue
                        smb.rmtree(unc_join(d, n)) if is_dir else smb.remove(unc_join(d, n))
                        removed.append(f"{folder}/{n}")
            else:
                for c in (Path(self.root) / folder).iterdir():
                    if c.name.endswith(LEFTOVER_SUFFIXES) and c.stat().st_mtime <= cutoff:
                        shutil.rmtree(c) if c.is_dir() else c.unlink()
                        removed.append(f"{folder}/{c.name}")
        return removed

    async def clean_leftovers(self) -> list[str]:
        """Remove unfinished files/folders of interrupted runs. Returns what was removed."""
        if not self.enabled:
            return []
        return await asyncio.to_thread(self._clean_leftovers_sync)

    async def prune(self) -> list[str]:
        """Keep only the newest N date folders. Only folders named like a Jalali
        date are ever touched."""
        keep = int(self.store.get_setting("archive_keep_days") or 14)
        return await asyncio.to_thread(self._prune_sync, keep)

    def _test_sync(self) -> str:
        probe = ".backuper-write-test"
        if self.is_smb:
            root = self._root_unc()
            with self._smb() as smb:
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
