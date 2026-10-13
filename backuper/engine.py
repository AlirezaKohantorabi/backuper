"""Backup run orchestration.

A run (a group at its time, one source at its own time, or "Run now") has three phases:
  1. collect + zip + archive every source (several in parallel),
  2. upload the run's zips to Telegram smallest first, one after another, so
     one huge file never holds the rest back. Uploads of all runs share one
     queue: two files are never uploaded at the same time. Archive-only
     sources don't hold the upload phase back,
  3. source retention (only for data that is safely stored), archive
     retention, report.
Runs may overlap (e.g. a 19:00 source while the 08:00 group is still
uploading); a source that is already running in another run is skipped.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import jdatetime

from backuper import jalali
from backuper.config import AppConfig
from backuper.db import Store
from backuper.destinations.archive import Archive
from backuper.destinations.telegram import TelegramSender, esc
from backuper.models import (
    FAILED, OK, PARTIAL, SKIPPED, STATUS_ICON, WARNING, Artifact, ItemResult, PackagedFile, SourceRecord,
)
from backuper.packager import build_zip, write_zip
from backuper.settings import FOLDER_FORMATS
from backuper.sources import RunContext, SourceError, SourceType, get_type
from backuper.util import hashtag, human_duration, human_size, safe_filename, short_error

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]
MIN_FREE_BYTES = 1024 * 1024 * 1024


@dataclass
class SourceProgress:
    name: str
    phase: str = "waiting"
    done_bytes: int = 0
    total_bytes: int = 0


@dataclass
class RunProgress:
    run_id: int
    trigger: str
    started: float = field(default_factory=time.monotonic)
    phase: str = "collecting"
    sources: dict[int, SourceProgress] = field(default_factory=dict)
    upload_index: int = 0
    upload_total: int = 0
    upload_name: str = ""
    upload_done: int = 0
    upload_size: int = 0

    def render(self) -> str:
        lines = [f"⏳ <b>Run #{self.run_id}</b> ({esc(self.trigger)}) — {self.phase}, "
                 f"{human_duration(time.monotonic() - self.started)}"]
        if self.phase == "collecting":
            for sp in self.sources.values():
                pct = f" {sp.done_bytes * 100 // sp.total_bytes}%" if sp.total_bytes and sp.phase == "zipping" else ""
                lines.append(f"• {esc(sp.name)}: {sp.phase}{pct}")
        elif self.phase == "uploading" and self.upload_total:
            pct = self.upload_done * 100 // self.upload_size if self.upload_size else 0
            lines.append(f"📤 {self.upload_index}/{self.upload_total}: {esc(self.upload_name)} "
                         f"({human_size(self.upload_size)}) {pct}%")
        return "\n".join(lines)


@dataclass
class _Pending:
    """In-memory bits of a source run needed after the upload phase."""

    plugin: SourceType
    stored: list[tuple[Artifact, PackagedFile]] = field(default_factory=list)


class Engine:
    def __init__(self, cfg: AppConfig, store: Store, sender: TelegramSender | None):
        self.cfg = cfg
        self.store = store
        self.sender = sender
        self.archive = Archive(store)
        self._upload_lock = asyncio.Lock()     # one upload at a time, across all runs
        self._active: dict[int, int] = {}      # source id -> run id currently processing it
        self.runs: dict[int, RunProgress] = {}

    @property
    def busy(self) -> bool:
        return bool(self.runs)

    def render_status(self) -> str:
        return "\n\n".join(p.render() for p in self.runs.values())

    # -- housekeeping -----------------------------------------------------------
    def startup_cleanup(self) -> None:
        self.store.mark_stale_runs()
        for d in self.cfg.staging_dir.glob("run-*"):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(self.cfg.staging_dir / "resend", ignore_errors=True)

    def _prune_unsent(self) -> None:
        keep_days = int(self.store.get_setting("archive_keep_days") or 14)
        cutoff = time.time() - keep_days * 86400
        for f in (self.cfg.staging_dir / "unsent").glob("*"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)

    # -- public API --------------------------------------------------------------------
    async def run(self, source_ids: list[int] | None = None, trigger: str = "manual",
                  on_progress: ProgressCallback | None = None, upload_from: str = "") -> int:
        """Run the given sources (None = every enabled source). Returns the run id.

        upload_from "HH:MM": don't start uploading before the next occurrence of that time
        (used by groups with an "Upload from" time; manual runs upload right away)."""
        tz = self.cfg.timezone
        now = jalali.now_jalali(tz)
        folder = jalali.folder_name(now, FOLDER_FORMATS.get(self.store.get_setting("folder_format"), "%Y-%m-%d"))
        if source_ids is None:
            sources = self.store.list_sources(enabled_only=True)
        else:
            sources = [s for s in (self.store.get_source(i) for i in source_ids) if s is not None]
        run_id = self.store.create_run(trigger, folder)
        results: list[ItemResult] = []
        for src in [s for s in sources if s.id in self._active]:
            results.append(ItemResult(source_id=src.id, source_name=src.name, status=SKIPPED,
                                      message=f"already running in run #{self._active[src.id]}"))
        sources = [s for s in sources if s.id not in self._active]
        upload_not_before = self._next_time(upload_from) if upload_from else None
        for src in sources:
            self._active[src.id] = run_id
        run_dir = self.cfg.staging_dir / f"run-{run_id}"
        prog = RunProgress(run_id=run_id, trigger=trigger,
                           sources={s.id: SourceProgress(s.name) for s in sources})
        self.runs[run_id] = prog
        started = time.monotonic()
        log.info("Run #%d (%s) started: %d source(s), folder %s", run_id, trigger, len(sources), folder)

        stop_ticker = asyncio.Event()
        ticker = asyncio.create_task(self._ticker(prog, on_progress, stop_ticker)) if on_progress else None
        pending: dict[int, _Pending] = {}
        tasks: dict[int, asyncio.Task] = {}
        archive_note = ""
        try:
            # Phase 1 — collect, zip, archive (several sources in parallel)
            sem = asyncio.Semaphore(int(self.store.get_setting("concurrency") or 3))
            tasks = {src.id: asyncio.create_task(self._process_source(src, now, folder, run_dir, sem, prog, pending))
                     for src in sources}
            # Phase 2 — as soon as every Telegram-bound source is ready: upload, smallest first.
            # Archive-only sources (e.g. huge folders) keep zipping meanwhile.
            tg = [tasks[s.id] for s in sources if self._uploads(s)]
            tg_results = list(await asyncio.gather(*tg))
            await self._wait_until(upload_not_before, prog)
            if self._upload_lock.locked():
                prog.phase = "waiting for another run's uploads"
            async with self._upload_lock:
                prog.phase = "uploading"
                await self._upload_all(tg_results, now, prog)
            prog.phase = "finishing"
            results += list(await asyncio.gather(*tasks.values()))

            # Phase 3 — retention + status
            for res in results:
                await self._finish_source(res, pending.get(res.source_id))
            if self.archive.enabled:
                try:
                    pruned = await self.archive.prune()
                    if pruned:
                        archive_note = f"🧹 archive: removed {', '.join(pruned)}"
                except Exception as e:  # noqa: BLE001
                    archive_note = f"⚠️ archive retention failed: {esc(short_error(e))}"
                    log.exception("archive prune failed")
            self._prune_unsent()
        except BaseException as e:
            for t in tasks.values():
                t.cancel()
            if isinstance(e, asyncio.CancelledError):
                raise
            log.exception("Run #%d crashed", run_id)
            done = {r.source_id for r in results}
            for t_id, t in tasks.items():
                if t_id not in done and t.done() and not t.cancelled() and t.exception() is None:
                    results.append(t.result())
            for res in results:
                if res.status == OK and not res.files:
                    res.status, res.message = FAILED, f"run aborted: {short_error(e)}"
            archive_note = f"💥 run aborted: {esc(short_error(e))}"
        finally:
            stop_ticker.set()
            if ticker:
                await ticker
            self._keep_unsent(results)
            shutil.rmtree(run_dir, ignore_errors=True)
            for src in sources:
                self._active.pop(src.id, None)
            self.runs.pop(run_id, None)

        duration = time.monotonic() - started
        status, report = self._report(run_id, trigger, now, results, duration, archive_note)
        total = sum(r.total_size for r in results)
        for res in results:
            self.store.add_run_item(run_id, res.source_id, res.source_name, res.status,
                                    "; ".join(filter(None, [res.message, *res.warnings])),
                                    [self._file_dict(f) for f in res.files], res.total_size,
                                    (res.finished - res.started) if res.started else 0)
        self.store.finish_run(run_id, status, total, report)
        self.store.prune_runs()
        await self._publish_report(status, report)
        if on_progress:
            try:
                await on_progress(report)
            except Exception:  # noqa: BLE001
                log.debug("progress callback failed", exc_info=True)
        log.info("Run #%d finished: %s in %s", run_id, status, human_duration(duration))
        return run_id

    def _uploads(self, src: SourceRecord) -> bool:
        """Will this source produce Telegram uploads? (Others don't hold the upload phase back.)"""
        return (self.sender is not None and bool(src.config.get("telegram", True))
                and src.config.get("format") != "copy")

    def _next_time(self, hhmm: str) -> datetime | None:
        """Next occurrence of HH:MM from now (today if still ahead, else tomorrow)."""
        try:
            h, m = (int(x) for x in hhmm.split(":"))
        except ValueError:
            return None
        now = datetime.now(self.cfg.timezone)
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return target if target > now else target + timedelta(days=1)

    async def _wait_until(self, when: datetime | None, prog: RunProgress) -> None:
        if when is None:
            return
        while True:
            left = (when - datetime.now(self.cfg.timezone)).total_seconds()
            if left <= 0:
                return
            prog.phase = f"ready — uploads start at {when:%H:%M}"
            await asyncio.sleep(min(left, 30))

    # -- phase 1 -----------------------------------------------------------------------
    async def _process_source(self, src: SourceRecord, now: jdatetime.datetime, folder: str, run_dir: Path,
                              sem: asyncio.Semaphore, prog: RunProgress, pending: dict[int, _Pending]) -> ItemResult:
        res = ItemResult(source_id=src.id, source_name=src.name)
        sp = prog.sources[src.id]
        async with sem:
            res.started = time.time()
            ctx = RunContext(self.store, src, run_dir / f"src-{src.id}", now)
            try:
                cls = get_type(src.type)
                plugin = cls(src, ctx)
                pending[src.id] = _Pending(plugin)
                sp.phase = "collecting"
                artifacts = await plugin.collect()
                for art in artifacts:
                    pf = await self._package(src, cls, art, ctx, folder, sp)
                    res.files.append(pf)
                    pending[src.id].stored.append((art, pf))
                    res.warnings.extend(art.warnings)
                    for p in art.cleanup:  # raw downloads are no longer needed once zipped
                        Path(p).unlink(missing_ok=True)
                sp.phase = "ready"
                if not artifacts and ctx.notes:
                    res.status, res.message = SKIPPED, "; ".join(ctx.notes)
                    sp.phase = "nothing new"
                else:
                    res.warnings.extend(f"note: {n}" for n in ctx.notes)
            except SourceError as e:
                res.status, res.message = FAILED, str(e)
                sp.phase = "failed"
            except Exception as e:  # noqa: BLE001 - one source must never break the run
                log.exception("Source %s failed", src.name)
                res.status, res.message = FAILED, short_error(e)
                sp.phase = "failed"
            res.warnings.extend(ctx.warnings)
            res.finished = time.time()
        return res

    async def _package(self, src: SourceRecord, cls: type[SourceType], art: Artifact, ctx: RunContext,
                       folder: str, sp: SourceProgress) -> PackagedFile:
        parts = [safe_filename(src.name)]
        if art.label and art.label.lower() != src.name.lower():
            parts.append(safe_filename(art.label))
        parts.append(ctx.stamp)
        file_name = "_".join(parts) + ".zip"
        # Without Telegram configured (archive-only mode) every zip goes straight to the archive.
        send_tg = bool(src.config.get("telegram", True)) and self.sender is not None
        zip_opts = {
            "password": self.store.get_setting("zip_password") or "",
            "level": int(self.store.get_setting("compression_level") or 6),
            "skip_unreadable": bool(art.meta.get("skip_unreadable")),
        }
        sp.done_bytes, sp.total_bytes = 0, art.total_size

        def progress(n: int) -> None:
            sp.done_bytes = n

        pf = PackagedFile(
            source_id=src.id, source_name=src.name, category=cls.category,
            topic=(src.config.get("topic") or ""), label=art.label, file_name=file_name,
            size=0, local_path=None, entries_count=len(art.entries), raw_size=art.total_size,
            send_telegram=send_tg,
        )

        if art.meta.get("format") == "copy":
            # Plain folder copy (e.g. big file-server folders): no zip, archive only.
            if not self.archive.enabled:
                raise SourceError("'Store as: copy' needs an archive location")
            dirname = file_name[: -len(".zip")]
            sp.phase = "copying → archive"
            pf.file_name, pf.send_telegram = dirname, False
            pf.archive_rel, pf.size, warnings = await self.archive.copy_tree(
                art, folder, dirname, skip_unreadable=bool(art.meta.get("skip_unreadable")), progress=progress)
            art.warnings.extend(warnings)
            return pf

        if not send_tg:
            # Archive-only: stream the zip straight into the archive, no local copy.
            if not self.archive.enabled:
                raise SourceError("'Send to Telegram' is off and no archive location is set — "
                                  "this backup would go nowhere")
            sp.phase = "zipping → archive"
            pf.archive_rel, pf.size, warnings = await self.archive.write_direct(
                folder, file_name, lambda f: write_zip(art, f, progress=progress, **zip_opts))
            art.warnings.extend(warnings)
            return pf

        free = shutil.disk_usage(self.cfg.staging_dir).free
        if free < MIN_FREE_BYTES:
            raise SourceError(f"staging disk almost full ({human_size(free)} free in {self.cfg.staging_dir})")
        out = ctx.workdir / "zip" / file_name
        sp.phase = "zipping"
        try:
            warnings = await asyncio.to_thread(build_zip, art, out, progress=progress, **zip_opts)
        except OSError as e:
            if e.errno == 28:
                raise SourceError(f"staging disk full while zipping {file_name}") from e
            raise
        art.warnings.extend(warnings)
        pf.size, pf.local_path = out.stat().st_size, out
        if self.archive.enabled:
            sp.phase = "archiving"
            try:
                pf.archive_rel = await self.archive.store_file(out, folder, file_name)
            except Exception as e:  # noqa: BLE001
                log.exception("archiving %s failed", file_name)
                art.warnings.append(f"archive copy failed: {short_error(e)}")
        return pf

    # -- phase 2 ---------------------------------------------------------------------------
    async def _upload_all(self, results: list[ItemResult], now: jdatetime.datetime, prog: RunProgress) -> None:
        files = sorted((f for r in results for f in r.files if f.send_telegram), key=lambda f: f.size)
        prog.upload_total = len(files)
        if not files:
            return
        if self.sender is None:
            for f in files:
                f.upload_error = "Telegram is not configured"
            return
        retries = int(self.store.get_setting("upload_retries") or 3)
        for i, pf in enumerate(files, start=1):
            prog.upload_index, prog.upload_name, prog.upload_size, prog.upload_done = i, pf.file_name, pf.size, 0
            await self._upload_one(pf, now, retries, prog)

    async def _upload_one(self, pf: PackagedFile, now: jdatetime.datetime, retries: int,
                          prog: RunProgress | None = None) -> None:
        assert self.sender is not None
        try:
            if pf.local_path is None or not pf.local_path.exists():
                raise RuntimeError("zip is no longer on disk")
            topic = self.sender.topic_title(pf.category, pf.source_name, pf.topic)

            def captions(i: int, n: int) -> str:
                return self._caption(pf, now, i, n)

            def progress(done: int) -> None:
                if prog:
                    prog.upload_done = done

            await self.sender.upload_file(pf.local_path, pf.file_name, captions, topic, progress, retries)
            pf.uploaded = True
        except Exception as e:  # noqa: BLE001
            log.exception("upload of %s failed", pf.file_name)
            pf.upload_error = short_error(e)

    def _caption(self, pf: PackagedFile, now: jdatetime.datetime, part: int, parts: int) -> str:
        icon = {"databases": "🗄", "network": "🌐", "websites": "🌍", "files": "📁"}.get(pf.category, "📦")
        title = f"{icon} <b>{esc(pf.source_name)}</b>" + (f" · {esc(pf.label)}" if pf.label else "")
        size = human_size(pf.size)
        if pf.raw_size and pf.raw_size > pf.size * 1.1:
            size += f" (raw {human_size(pf.raw_size)})"
        lines = [title, f"📅 {jalali.pretty(now)}", f"📦 {size}"]
        if parts > 1:
            lines.append(f"🧩 part {part}/{parts} — put all parts in one folder and open .001 with 7-Zip")
        if self.store.get_setting("zip_password"):
            lines.append("🔐 AES-256")
        lines.append(" ".join(filter(None, [hashtag(pf.source_name), hashtag(pf.category)])))
        return "\n".join(lines)

    # -- phase 3 ----------------------------------------------------------------------------
    async def _finish_source(self, res: ItemResult, pend: _Pending | None) -> None:
        if res.status in (FAILED, SKIPPED):
            return
        if not res.files:
            res.status, res.message = WARNING, "nothing to back up"
            return
        archive_on = self.archive.enabled
        problems = [f"Telegram: {f.upload_error}" for f in res.files if f.upload_error]
        if archive_on and not all(f.archive_rel for f in res.files):
            problems.append("not all files reached the archive")
        if not any(f.archive_rel or f.uploaded for f in res.files):
            res.status = FAILED
        elif not all((f.uploaded or not f.send_telegram) and (f.archive_rel or not archive_on) for f in res.files):
            res.status = PARTIAL
        res.message = "; ".join(problems)
        if pend is not None and archive_on:
            await self._prune_source_archive(pend, res)

        # Source-side retention only for artifacts that are safely stored somewhere.
        if pend is not None:
            for a, pf in pend.stored:  # fully delivered = everywhere it should go (lets sources skip it next time)
                a.meta["delivered"] = bool((pf.archive_rel or not archive_on) and (pf.uploaded or not pf.send_telegram))
            safe = [a for a, pf in pend.stored if pf.archive_rel or (not self.archive.enabled and pf.uploaded)]
            if safe:
                try:
                    res.warnings.extend(await pend.plugin.after_success(safe))
                except Exception as e:  # noqa: BLE001
                    log.exception("retention for %s failed", res.source_name)
                    res.warnings.append(f"retention failed: {short_error(e)}")
        if res.status == OK and any(not w.startswith(("retention:", "deleted ", "note:")) for w in res.warnings):
            res.status = WARNING

    async def _prune_source_archive(self, pend: _Pending, res: ItemResult) -> None:
        """Per-source 'Archive copies to keep': remember which zips each run archived
        and delete the oldest runs' zips beyond N."""
        rels = [f.archive_rel for f in res.files if f.archive_rel]
        if not rels:
            return
        ctx = pend.plugin.ctx
        keep = int(pend.plugin.cfg.get("archive_keep") or 0)
        history: list[list[str]] = [h for h in ctx.get_state("archive_history", []) if h != rels] + [rels]
        if keep > 0 and len(history) > keep:
            # A re-run within the same minute overwrites a file with the same name: never delete
            # anything that also belongs to one of the copies we keep.
            protected = {r for h in history[-keep:] for r in h}
            for old_rels in history[:-keep]:
                for rel in old_rels:
                    if rel in protected:
                        continue
                    try:
                        if await self.archive.delete(rel):
                            res.warnings.append(f"retention: removed old archive copy {rel}")
                    except Exception as e:  # noqa: BLE001
                        res.warnings.append(f"couldn't remove old archive copy {rel}: {short_error(e)}")
            history = history[-keep:]
        ctx.set_state("archive_history", history[-60:])

    def _keep_unsent(self, results: list[ItemResult]) -> None:
        """A zip that reached neither the archive nor Telegram is kept in staging/unsent."""
        unsent_dir = self.cfg.staging_dir / "unsent"
        for res in results:
            for f in res.files:
                if f.local_path and f.local_path.exists() and not f.archive_rel and not f.uploaded:
                    unsent_dir.mkdir(parents=True, exist_ok=True)
                    dest = unsent_dir / f.file_name
                    shutil.move(str(f.local_path), dest)
                    f.local_path = dest
                    res.warnings.append(f"zip kept on the backup server: {dest}")
                else:
                    f.local_path = None

    # -- reporting ---------------------------------------------------------------------------
    def _report(self, run_id: int, trigger: str, now: jdatetime.datetime, results: list[ItemResult],
                duration: float, extra: str) -> tuple[str, str]:
        counts = {s: sum(1 for r in results if r.status == s) for s in (OK, SKIPPED, WARNING, PARTIAL, FAILED)}
        if not results:
            status = OK
        elif counts[FAILED] == len(results):
            status = FAILED
        elif counts[FAILED] or counts[PARTIAL]:
            status = PARTIAL
        elif counts[WARNING]:
            status = WARNING
        else:
            status = OK
        total = sum(r.total_size for r in results)
        head = (f"{STATUS_ICON[status]} <b>Backup report #{run_id}</b> — {jalali.pretty(now)} ({esc(trigger)})\n"
                + " · ".join(f"{STATUS_ICON[s]} {n}" for s, n in counts.items() if n)
                + f" · {human_size(total)} · {human_duration(duration)}")
        lines = [head, ""]
        for r in sorted(results, key=lambda r: (r.status in (OK, SKIPPED), r.source_name.lower())):
            line = f"{STATUS_ICON[r.status]} <b>{esc(r.source_name)}</b>"
            if r.files:
                line += " — " + ", ".join(
                    f"{esc(f.label) + ' ' if f.label else ''}{human_size(f.size)}" for f in r.files)
            if r.message:
                line += f"\n    ↳ {esc(r.message)}"
            for w in r.warnings[:5]:
                line += f"\n    · {esc(w)}"
            if len(r.warnings) > 5:
                line += f"\n    · … {len(r.warnings) - 5} more"
            lines.append(line)
        if extra:
            lines += ["", extra]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        return status, text

    async def _publish_report(self, status: str, report: str) -> None:
        if self.sender is None:
            return
        try:
            if status != OK or self.store.get_setting("report_on_success"):
                await self.sender.send_text(report, self.sender.topic_title("reports", "Reports"))
        except Exception:  # noqa: BLE001
            log.exception("couldn't post the report to the group")
        if status in (FAILED, PARTIAL, WARNING) and self.store.get_setting("notify_admins"):
            try:
                await self.sender.notify_admins(report)
            except Exception:  # noqa: BLE001
                log.exception("couldn't DM admins")

    @staticmethod
    def _file_dict(f: PackagedFile) -> dict:
        d = asdict(f)
        d["local_path"] = str(f.local_path) if f.local_path else None
        return d

    async def _ticker(self, prog: RunProgress, cb: ProgressCallback, stop: asyncio.Event) -> None:
        last = ""
        while not stop.is_set():
            text = prog.render()
            if text != last:
                try:
                    await cb(text)
                    last = text
                except Exception:  # noqa: BLE001
                    log.debug("progress update failed", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=6)
            except asyncio.TimeoutError:
                pass

    # -- resend -------------------------------------------------------------------------------------
    async def resend_item(self, item_id: int) -> str:
        """Re-upload the zips of a past run item (from the archive or the unsent folder)."""
        if self.sender is None:
            raise RuntimeError("Telegram is not configured")
        row = self.store.get_run_item(item_id)
        if row is None:
            raise RuntimeError("run item not found")
        import json

        files = [PackagedFile(**{**d, "local_path": Path(d["local_path"]) if d.get("local_path") else None})
                 for d in json.loads(row["files"])]
        if not files:
            return "That item has no files."
        async with self._upload_lock:
            tmp_dir = self.cfg.staging_dir / "resend"
            sent, failed = 0, []
            now = jalali.now_jalali(self.cfg.timezone)
            for pf in sorted(files, key=lambda f: f.size):
                fetched = None
                if not (pf.local_path and pf.local_path.exists()):
                    if not (pf.archive_rel and self.archive.enabled):
                        failed.append(f"{pf.file_name}: not in archive")
                        continue
                    fetched = tmp_dir / pf.file_name
                    try:
                        await self.archive.fetch(pf.archive_rel, fetched)
                    except Exception as e:  # noqa: BLE001
                        failed.append(f"{pf.file_name}: {short_error(e)}")
                        continue
                    pf.local_path = fetched
                pf.uploaded, pf.upload_error = False, ""
                await self._upload_one(pf, now, int(self.store.get_setting("upload_retries") or 3))
                if pf.uploaded:
                    sent += 1
                    if fetched is None and pf.local_path and "unsent" in pf.local_path.parts and pf.archive_rel:
                        pf.local_path.unlink(missing_ok=True)
                else:
                    failed.append(f"{pf.file_name}: {pf.upload_error}")
                if fetched:
                    fetched.unlink(missing_ok=True)
                    pf.local_path = None
            self.store.update_run_item_files(item_id, [self._file_dict(f) for f in files])
        msg = f"Re-sent {sent}/{len(files)} file(s)."
        if failed:
            msg += "\n" + "\n".join(failed)
        return msg
