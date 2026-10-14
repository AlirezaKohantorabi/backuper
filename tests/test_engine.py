"""End-to-end run with a fake source plugin, a local archive and a fake Telegram."""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pyzipper
import pytest

from backuper import jalali
from backuper.engine import Engine
from backuper.fields import INT, Field
from backuper.models import FAILED, OK, PARTIAL, Artifact, Entry
from backuper.sources import REGISTRY, SourceError, SourceType


class FakeSource(SourceType):
    type_id = "fake"
    title = "Fake"
    category = "databases"
    fields = [Field("sizes", "Sizes", INT, default=0), Field("fail", "Fail", INT, default=0)]
    retained: list[str] = []

    gate: asyncio.Event | None = None  # set by tests to hold a source in "collecting"

    async def collect(self):
        if self.cfg.get("copy"):
            data = b"hello world"
            return [Artifact(label="", meta={"format": "copy"}, entries=[
                Entry(arcname=f"sub/f{i}.txt", size=len(data), mtime=1.7e9, opener=lambda: io.BytesIO(data))
                for i in range(3)])]
        if self.cfg.get("hold") and FakeSource.gate is not None:
            await FakeSource.gate.wait()
        if self.cfg.get("fail"):
            raise SourceError("server unreachable")
        arts = []
        for i, size in enumerate(self.cfg["sizes"]):
            data = os.urandom(size)  # incompressible => zip size ~ input size
            arts.append(Artifact(label=f"db{i}", entries=[
                Entry(arcname=f"db{i}.bak", size=size, mtime=1.7e9, opener=lambda d=data: io.BytesIO(d))]))
        return arts

    async def after_success(self, artifacts):
        FakeSource.retained += [f"{self.record.name}/{a.label}" for a in artifacts]
        return ["retention: deleted 1 old file(s), keeping 7"]


class FakeSender:
    def __init__(self, fail: set[str] | None = None):
        self.uploads: list[tuple[str, int, str | None]] = []
        self.texts: list[str] = []
        self.fail = fail or set()

    def topic_title(self, category, source_name, override=""):
        return override or category

    async def upload_file(self, path: Path, file_name, captions, topic, progress=None, retries=3):
        if any(f in file_name for f in self.fail):
            raise RuntimeError("network down")
        self.uploads.append((file_name, path.stat().st_size, topic))
        assert "<b>" in captions(1, 1)
        return 1

    async def send_text(self, text, topic=None):
        self.texts.append(text)

    async def notify_admins(self, text):
        self.texts.append("DM:" + text)


@pytest.fixture(autouse=True)
def fake_type(monkeypatch):
    monkeypatch.setitem(REGISTRY, "fake", FakeSource)
    FakeSource.retained = []


async def test_full_run(cfg, store, tmp_path):
    archive = tmp_path / "archive"
    for old in ("1405-07-01", "1405-07-02", "1405-07-03", "1405-07-04"):
        (archive / old).mkdir(parents=True)
    (archive / "keep-me-not-a-date").mkdir()
    store.set_setting("archive_path", str(archive))
    store.set_setting("archive_keep_days", 3)
    store.set_setting("zip_password", "pw")

    store.create_source("Big", "fake", {"sizes": [400_000]}, set())
    store.create_source("Small", "fake", {"sizes": [5_000, 90_000]}, set())
    store.create_source("Broken", "fake", {"sizes": [], "fail": 1}, set())

    sender = FakeSender()
    engine = Engine(cfg, store, sender)
    run_id = await engine.run(trigger="test")

    # smallest first, across all sources
    sizes = [s for _, s, _ in sender.uploads]
    assert sizes == sorted(sizes) and len(sizes) == 3
    assert sender.uploads[-1][0].startswith("Big_db0_")

    today = jalali.folder_name(jalali.now_jalali(cfg.timezone))
    zips = sorted(p.name for p in (archive / today).iterdir())
    assert len(zips) == 3 and all(z.endswith(".zip") for z in zips)
    with pyzipper.AESZipFile(archive / today / zips[0]) as z:
        z.setpassword(b"pw")
        assert z.testzip() is None

    # archive retention: newest 3 date folders survive, non-date folders untouched
    remaining = sorted(p.name for p in archive.iterdir())
    assert remaining == sorted(["1405-07-03", "1405-07-04", today, "keep-me-not-a-date"])

    run = store.get_run(run_id)
    assert run["status"] == PARTIAL
    items = {i["source_name"]: i for i in store.run_items(run_id)}
    assert items["Broken"]["status"] == FAILED and "unreachable" in items["Broken"]["message"]
    assert items["Small"]["status"] == OK and items["Big"]["status"] == OK
    assert sorted(FakeSource.retained) == ["Big/db0", "Small/db0", "Small/db1"]
    assert any("Backup report" in t for t in sender.texts)
    assert not list(cfg.staging_dir.glob("run-*"))  # staging cleaned


async def test_upload_failure_keeps_archive_and_skips_nothing(cfg, store, tmp_path):
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.create_source("A", "fake", {"sizes": [1000]}, set())
    sender = FakeSender(fail={"A_"})
    engine = Engine(cfg, store, sender)
    run_id = await engine.run(trigger="test")
    item = store.run_items(run_id)[0]
    assert item["status"] == PARTIAL and "Telegram" in item["message"]
    # archived, so retention still ran
    assert FakeSource.retained == ["A/db0"]

    # resend from the archive once Telegram is back
    sender.fail.clear()
    msg = await engine.resend_item(item["id"])
    assert "1/1" in msg and sender.uploads[0][0].startswith("A_db0_")


async def test_no_archive_and_no_telegram_keeps_zip_locally(cfg, store):
    store.create_source("A", "fake", {"sizes": [1000]}, set())
    engine = Engine(cfg, store, FakeSender(fail={"A_"}))
    run_id = await engine.run(trigger="test")
    item = store.run_items(run_id)[0]
    assert item["status"] == FAILED
    assert FakeSource.retained == []  # never delete source data that isn't stored anywhere
    assert list((cfg.staging_dir / "unsent").glob("A_*.zip"))


async def test_archive_only_source_streams_into_archive_and_keeps_n(cfg, store, tmp_path, monkeypatch):
    import jdatetime

    minutes = iter(range(10, 59))
    monkeypatch.setattr(jalali, "now_jalali",
                        lambda tz: jdatetime.datetime(1405, 7, 18, 3, next(minutes)))
    archive = tmp_path / "arch"
    store.set_setting("archive_path", str(archive))
    store.set_setting("zip_password", "pw")
    sid = store.create_source("Edari", "fake", {"sizes": [50_000], "telegram": False, "archive_keep": 2}, set())
    store.create_source("DB", "fake", {"sizes": [1_000]}, set())
    sender = FakeSender()
    engine = Engine(cfg, store, sender)

    for _ in range(3):
        run_id = await engine.run(trigger="test")
        assert {i["source_name"]: i["status"] for i in store.run_items(run_id)} == {"Edari": OK, "DB": OK}

    # never sent to Telegram, only the DB zips were
    assert sender.uploads and all(name.startswith("DB_") for name, _, _ in sender.uploads)
    # no zip was ever written to the local staging area for the archive-only source
    assert not list(cfg.staging_dir.rglob("Edari_*"))
    # per-source retention: only the newest 2 Edari zips remain (all runs land in today's folder)
    edari = sorted(archive.rglob("Edari_*.zip"))
    assert len(edari) == 2 and len(list(archive.rglob("DB_*.zip"))) == 3
    assert not list(archive.rglob("*.uploading"))
    with pyzipper.AESZipFile(edari[-1]) as z:
        z.setpassword(b"pw")
        assert z.testzip() is None
    assert len(store.get_source(sid).state["archive_history"]) == 2


async def test_archive_only_without_archive_fails_clearly(cfg, store):
    store.create_source("Edari", "fake", {"sizes": [10], "telegram": False}, set())
    run_id = await Engine(cfg, store, FakeSender()).run(trigger="test")
    item = store.run_items(run_id)[0]
    assert item["status"] == FAILED and "no archive location" in item["message"]


async def test_same_minute_rerun_never_deletes_the_current_copy(cfg, store, tmp_path, monkeypatch):
    import jdatetime

    monkeypatch.setattr(jalali, "now_jalali", lambda tz: jdatetime.datetime(1405, 7, 18, 3, 0))
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.create_source("Edari", "fake", {"sizes": [100], "telegram": False, "archive_keep": 1}, set())
    engine = Engine(cfg, store, FakeSender())
    for _ in range(3):
        await engine.run(trigger="test")
    assert len(list((tmp_path / "arch").rglob("Edari_*.zip"))) == 1


async def test_collect_skips_backup_that_was_already_stored(cfg, store, tmp_path, monkeypatch):
    """A share whose newest .bak didn't change since the last run must not be re-sent."""
    import time as _t

    from backuper.models import SKIPPED
    from backuper.sources import mssql_collect
    from backuper.transport.smb import RemoteFile

    data = os.urandom(2000)
    newest = RemoteFile(path=r"\\h\s\HS_X_14050718_090112.bak", rel="HS_X_14050718_090112.bak",
                        size=len(data), mtime=_t.time() - 3600)

    class FakeSMB:
        def opener(self, path):
            return lambda: io.BytesIO(data)

    async def fake_groups(self):
        return FakeSMB(), {"HS_X_*.bak": [newest]}

    async def passthrough(smb, f, ctx, max_wait_s=0):
        return f

    monkeypatch.setattr(mssql_collect.MSSQLCollect, "_groups", fake_groups)
    monkeypatch.setattr(mssql_collect, "wait_until_stable", passthrough)
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.save_credential("c", "u", "p")
    store.create_source("Hamrahan", "mssql_collect", {"share": r"\\h\s", "credential": "c",
                                                      "patterns": ["HS_X_*.bak"], "max_age_hours": 26}, set())
    sender = FakeSender()
    engine = Engine(cfg, store, sender)

    first = store.run_items(await engine.run(trigger="test"))[0]
    assert first["status"] == OK and len(sender.uploads) == 1
    assert sender.uploads[0][0].startswith("Hamrahan_HS_X_")

    second = store.run_items(await engine.run(trigger="test"))[0]
    assert second["status"] == SKIPPED and "no new backup" in second["message"]
    assert len(sender.uploads) == 1  # nothing re-uploaded

    newest.mtime = _t.time() - 1800  # a new backup appears
    third = store.run_items(await engine.run(trigger="test"))[0]
    assert third["status"] == OK and len(sender.uploads) == 2


async def test_collect_retries_upload_next_run_if_telegram_failed(cfg, store, tmp_path, monkeypatch):
    import time as _t

    from backuper.sources import mssql_collect
    from backuper.transport.smb import RemoteFile

    newest = RemoteFile(path=r"\\h\s\X_1.bak", rel="X_1.bak", size=10, mtime=_t.time() - 60)

    class FakeSMB:
        def opener(self, path):
            return lambda: io.BytesIO(b"0123456789")

    async def fake_groups(self):
        return FakeSMB(), {"X_*.bak": [newest]}

    async def passthrough(smb, f, ctx, max_wait_s=0):
        return f

    monkeypatch.setattr(mssql_collect.MSSQLCollect, "_groups", fake_groups)
    monkeypatch.setattr(mssql_collect, "wait_until_stable", passthrough)
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.save_credential("c", "u", "p")
    store.create_source("S", "mssql_collect", {"share": r"\\h\s", "credential": "c", "patterns": ["X_*.bak"]}, set())
    sender = FakeSender(fail={"S_"})
    engine = Engine(cfg, store, sender)
    assert store.run_items(await engine.run(trigger="t"))[0]["status"] == PARTIAL   # archived, Telegram down
    sender.fail.clear()
    assert store.run_items(await engine.run(trigger="t"))[0]["status"] == OK        # re-sent automatically
    assert len(sender.uploads) == 1


class SlowSender(FakeSender):
    """Uploads take a moment; records how many ran at the same time."""

    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def upload_file(self, path, file_name, captions, topic, progress=None, retries=3):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.uploads.append((file_name, path.stat().st_size, topic))
        self.active -= 1
        return 1


async def test_overlapping_runs_never_upload_at_the_same_time(cfg, store):
    a = store.create_source("GroupA", "fake", {"sizes": [30_000, 1_000, 9_000]}, set())
    b = store.create_source("Single", "fake", {"sizes": [500, 20_000]}, set())
    sender = SlowSender()
    engine = Engine(cfg, store, sender)
    r1, r2 = await asyncio.gather(engine.run([a], trigger="group"), engine.run([b], trigger="single"))
    assert sender.max_active == 1                       # one upload at a time, across runs
    names = [n for n, _, _ in sender.uploads]
    first_run = [n for n in names if n.startswith("GroupA")]
    assert [sz for n, sz, _ in sender.uploads if n.startswith("GroupA")] == sorted(
        sz for n, sz, _ in sender.uploads if n.startswith("GroupA"))  # smallest first within the run
    # each run's uploads stay together (no interleaving)
    idx = [names.index(n) for n in first_run]
    assert idx == list(range(min(idx), min(idx) + len(idx)))
    assert {i["status"] for r in (r1, r2) for i in store.run_items(r)} == {OK}


async def test_archive_only_source_does_not_delay_uploads(cfg, store, tmp_path):
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.create_source("Big-folder", "fake", {"sizes": [100], "telegram": False, "hold": True}, set())
    store.create_source("DB", "fake", {"sizes": [100]}, set())
    FakeSource.gate = asyncio.Event()
    sender = FakeSender()
    engine = Engine(cfg, store, sender)
    task = asyncio.create_task(engine.run(trigger="t"))
    for _ in range(100):                     # DB gets uploaded while Big-folder is still "zipping"
        if sender.uploads:
            break
        await asyncio.sleep(0.01)
    assert [n for n, _, _ in sender.uploads][0].startswith("DB_") and not task.done()
    FakeSource.gate.set()
    run_id = await task
    FakeSource.gate = None
    assert {i["source_name"]: i["status"] for i in store.run_items(run_id)} == {"Big-folder": OK, "DB": OK}


async def test_source_already_running_is_skipped(cfg, store):
    sid = store.create_source("Busy", "fake", {"sizes": [10], "hold": True}, set())
    FakeSource.gate = asyncio.Event()
    engine = Engine(cfg, store, FakeSender())
    first = asyncio.create_task(engine.run([sid], trigger="group"))
    await asyncio.sleep(0.05)
    assert engine.busy and "Busy" in engine.render_status()
    second = await engine.run([sid], trigger="manual")
    item = store.run_items(second)[0]
    from backuper.models import SKIPPED
    assert item["status"] == SKIPPED and "already running" in item["message"]
    FakeSource.gate.set()
    await first
    FakeSource.gate = None
    assert not engine.busy


async def test_upload_waits_for_upload_from_time(cfg, store, monkeypatch):
    from datetime import datetime, timedelta

    store.create_source("DB", "fake", {"sizes": [100]}, set())
    sender = FakeSender()
    engine = Engine(cfg, store, sender)
    soon = datetime.now(cfg.timezone) + timedelta(seconds=1.2)
    waited = []
    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        waited.append(sec)
        await real_sleep(min(sec, 0.3))
    monkeypatch.setattr("backuper.engine.asyncio.sleep", fast_sleep)
    await engine.run(trigger="group", upload_from="")
    assert sender.uploads and not waited                      # no upload time -> no waiting
    target = engine._next_time(soon.strftime("%H:%M"))
    assert target is not None and timedelta(0) <= target - datetime.now(cfg.timezone) <= timedelta(days=1)
    sender.uploads.clear()
    monkeypatch.setattr(engine, "_next_time", lambda hhmm: soon)
    await engine.run(trigger="group", upload_from="08:00")
    assert waited and sender.uploads                           # waited, then uploaded
    assert datetime.now(cfg.timezone) >= soon


async def test_archive_only_mode_without_telegram(cfg, store, tmp_path):
    """Before Telegram is configured, runs still fill the archive and count as OK."""
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.create_source("DB", "fake", {"sizes": [1000]}, set())
    run_id = await Engine(cfg, store, None).run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == OK and len(list((tmp_path / "arch").rglob("DB_*.zip"))) == 1
    assert not list(cfg.staging_dir.rglob("DB_*.zip"))  # streamed straight into the archive


async def test_copy_format_makes_a_plain_dated_folder(cfg, store, tmp_path):
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))
    store.set_setting("zip_password", "pw")
    store.create_source("Edari", "fake", {"copy": True, "archive_keep": 1}, set())
    sender = FakeSender()
    engine = Engine(cfg, store, sender)
    run_id = await engine.run(trigger="t")
    assert store.run_items(run_id)[0]["status"] == OK and not sender.uploads   # never sent to Telegram
    dirs = [p for p in arch.rglob("Edari_*") if p.is_dir()]
    assert len(dirs) == 1 and sorted(x.name for x in (dirs[0] / "sub").iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]
    assert (dirs[0] / "sub" / "f0.txt").read_bytes() == b"hello world"          # plain, not encrypted
    assert int((dirs[0] / "sub" / "f0.txt").stat().st_mtime) == int(1.7e9)    # original date kept
    assert not list(arch.rglob("*.partial"))


async def test_archive_write_is_retried(cfg, store, tmp_path, monkeypatch):
    """A failed write into the archive (network hiccup) is retried; the run still ends OK."""
    monkeypatch.setattr("backuper.engine.RETRY_WAITS", (0, 0))
    store.set_setting("archive_path", str(tmp_path / "arch"))
    store.create_source("DB", "fake", {"sizes": [1000], "telegram": False}, set())
    engine = Engine(cfg, store, None)
    real, calls = engine.archive.write_direct, []

    async def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("Request requires 1 credits but only 0 credits are available")
        return await real(*a, **kw)

    monkeypatch.setattr(engine.archive, "write_direct", flaky)
    run_id = await engine.run(trigger="t")
    item = store.run_items(run_id)[0]
    assert len(calls) == 2 and item["status"] == OK and "attempt 2" in item["message"]
    assert len(list((tmp_path / "arch").rglob("DB_*.zip"))) == 1


async def test_folder_copy_resumes_after_a_failure(cfg, store, tmp_path, monkeypatch):
    """A copy that breaks half-way is retried and only copies what's missing."""
    monkeypatch.setattr("backuper.engine.RETRY_WAITS", (0, 0))
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))
    opened: list[str] = []

    def opener(name: str):
        def open_():
            opened.append(name)
            if name == "f1" and opened.count("f1") == 1:
                raise ConnectionResetError("connection lost")  # not "unreadable": fails the attempt
            return io.BytesIO(b"data")
        return open_

    async def collect(self):
        return [Artifact(label="", meta={"format": "copy", "skip_unreadable": True}, entries=[
            Entry(arcname=f"d/{n}.txt", size=4, mtime=1.7e9, opener=opener(n)) for n in ("f0", "f1", "f2")])]

    monkeypatch.setattr(FakeSource, "collect", collect)
    store.create_source("Edari", "fake", {}, set())
    run_id = await Engine(cfg, store, None).run(trigger="t")
    assert store.run_items(run_id)[0]["status"] == OK
    assert opened == ["f0", "f1", "f1", "f2"]  # f0 wasn't copied twice
    dirs = [p for p in arch.rglob("Edari_*") if p.is_dir()]
    assert len(dirs) == 1 and sorted(x.name for x in (dirs[0] / "d").iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]
    assert not list(arch.rglob("*.partial"))


async def test_folder_copy_that_keeps_failing_leaves_nothing_behind(cfg, store, tmp_path, monkeypatch):
    monkeypatch.setattr("backuper.engine.RETRY_WAITS", (0, 0))
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))

    def broken():
        raise ConnectionResetError("connection lost")

    async def collect(self):
        return [Artifact(label="", meta={"format": "copy", "skip_unreadable": True}, entries=[
            Entry(arcname="a.txt", size=4, mtime=1.7e9, opener=lambda: io.BytesIO(b"data")),
            Entry(arcname="b.txt", size=4, mtime=1.7e9, opener=broken)])]

    monkeypatch.setattr(FakeSource, "collect", collect)
    store.create_source("Edari", "fake", {}, set())
    run_id = await Engine(cfg, store, None).run(trigger="t")
    assert store.run_items(run_id)[0]["status"] == FAILED
    assert not list(arch.rglob("*.partial")) and not list(arch.rglob("Edari_*"))


async def test_unreadable_file_is_skipped_not_retried(cfg, store, tmp_path, monkeypatch):
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))

    def denied():
        raise PermissionError("STATUS_ACCESS_DENIED")

    async def collect(self):
        return [Artifact(label="", meta={"format": "copy", "skip_unreadable": True}, entries=[
            Entry(arcname="a.txt", size=4, mtime=1.7e9, opener=lambda: io.BytesIO(b"data")),
            Entry(arcname="locked.xlsx", size=4, mtime=1.7e9, opener=denied)])]

    monkeypatch.setattr(FakeSource, "collect", collect)
    store.create_source("Edari", "fake", {}, set())
    run_id = await Engine(cfg, store, None).run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == "warning" and "skipped locked.xlsx" in item["message"]
    dirs = [p for p in arch.rglob("Edari_*") if p.is_dir()]
    assert [x.name for x in dirs[0].iterdir()] == ["a.txt"]


async def test_startup_removes_leftovers_of_interrupted_runs(cfg, store, tmp_path):
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))
    day = arch / "1405-07-22"
    (day / "Edari_1405-07-22_0700.partial" / "sub").mkdir(parents=True)
    (day / "Cash_1405-07-22_0800.zip.uploading").write_bytes(b"x")
    (day / "Arpa_1405-07-22_0800.zip").write_bytes(b"ok")
    (day / "New_1405-07-22_1100.zip.uploading").write_bytes(b"x")  # being written right now
    old = 1.7e9
    for p in (day / "Edari_1405-07-22_0700.partial", day / "Cash_1405-07-22_0800.zip.uploading"):
        os.utime(p, (old, old))
    await Engine(cfg, store, None).clean_archive_leftovers()
    assert sorted(p.name for p in day.iterdir()) == ["Arpa_1405-07-22_0800.zip", "New_1405-07-22_1100.zip.uploading"]
