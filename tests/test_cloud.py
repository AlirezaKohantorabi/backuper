"""Cloud copy (replaces Telegram uploads), run end to end with the real rclone against a local folder."""

from __future__ import annotations

import hashlib
import shutil

import pytest

from backuper.destinations.cloud import Cloud, CloudError, drive_authorize_command, rclone_binary
from backuper.engine import Engine
from backuper.models import OK, PARTIAL
from backuper.sources import REGISTRY
from tests.test_engine import FakeSource

try:
    rclone_binary()
    HAVE_RCLONE = True
except CloudError:
    HAVE_RCLONE = False
needs_rclone = pytest.mark.skipif(not HAVE_RCLONE, reason="rclone not installed")


@pytest.fixture(autouse=True)
def fake_type(monkeypatch):
    monkeypatch.setitem(REGISTRY, "fake", FakeSource)


def test_authorize_command_matches_rclone():
    # The exact command `rclone config` prints for a headless Drive setup with scope drive.file.
    assert drive_authorize_command() == 'rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"'
    assert '"drive" "' in drive_authorize_command("id.apps.googleusercontent.com", "secret")


def _setup(store, tmp_path, keep_days=14):
    arch, cloud = tmp_path / "arch", tmp_path / "cloud"
    store.set_setting("archive_path", str(arch))
    store.set_setting("cloud_type", "local")
    store.set_setting("cloud_folder", str(cloud))
    store.set_setting("cloud_keep_days", keep_days)
    return arch, cloud


@needs_rclone
async def test_run_copies_its_zips_to_the_cloud(cfg, store, tmp_path):
    arch, cloud = _setup(store, tmp_path)
    store.create_source("DB", "fake", {"sizes": [3000, 1000], "telegram": False}, set())
    store.create_source("Private", "fake", {"sizes": [500], "telegram": False, "cloud": False}, set())
    store.create_source("Edari", "fake", {"copy": True}, set())                 # plain folder copy: never sent
    run_id = await Engine(cfg, store, None).run(trigger="t")

    items = {i["source_name"]: i for i in store.run_items(run_id)}
    assert all(i["status"] == OK for i in items.values())
    sent = sorted(p.relative_to(cloud).as_posix() for p in cloud.rglob("*") if p.is_file())
    assert len(sent) == 2 and all("/DB_db" in s for s in sent)                 # not Private, not Edari
    for rel in sent:                                                            # byte-identical to the archive
        assert hashlib.md5((cloud / rel).read_bytes()).hexdigest() == hashlib.md5((arch / rel).read_bytes()).hexdigest()
    assert '"cloud": true' in items["DB"]["files"] and "☁ cloud: uploaded 2 zip(s)" in store.get_run(run_id)["summary"]


@needs_rclone
async def test_missing_cloud_files_are_caught_up_and_old_days_removed(cfg, store, tmp_path):
    arch, cloud = _setup(store, tmp_path, keep_days=2)
    for day in ("1405-07-20", "1405-07-21"):                                   # older archive days
        (arch / day).mkdir(parents=True)
        (arch / day / f"DB_db0_{day}_0800.zip").write_bytes(b"old " + day.encode())
    (cloud / "1405-07-20").mkdir(parents=True)
    (cloud / "1405-07-20" / "DB_db0_1405-07-20_0800.zip").write_bytes(b"old 1405-07-20")
    (cloud / "not-a-date").mkdir()
    store.create_source("DB", "fake", {"sizes": [1000], "telegram": False}, set())
    engine = Engine(cfg, store, None)
    await engine.run(trigger="t")

    days = sorted(p.name for p in cloud.iterdir())
    today = sorted(p.name for p in arch.iterdir())[-1]
    assert days == sorted(["1405-07-21", today, "not-a-date"])                 # 07-20 removed, 07-21 caught up
    res = await engine.cloud.sync(set())
    assert not res.uploaded and not res.failed and res.already == 2            # nothing left to do


@needs_rclone
async def test_failed_upload_makes_the_source_partial_and_is_retried(cfg, store, tmp_path, monkeypatch):
    arch, cloud = _setup(store, tmp_path)
    store.create_source("DB", "fake", {"sizes": [1000], "telegram": False}, set())
    engine = Engine(cfg, store, None)
    real = Cloud._archive_md5
    monkeypatch.setattr(Cloud, "_archive_md5", lambda self, rel: "0" * 32)    # pretend the upload got damaged
    run_id = await engine.run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == PARTIAL and "cloud:" in item["message"]
    assert not list(cloud.rglob("*.zip"))                                     # the bad copy was deleted
    monkeypatch.setattr(Cloud, "_archive_md5", real)
    res = await engine.cloud.sync(set())
    assert len(res.uploaded) == 1 and not res.failed                           # the next run does it


def test_cloud_is_off_by_default(cfg, store):
    assert not Cloud(store, Engine(cfg, store, None).archive).enabled


@needs_rclone
async def test_cloud_test_command(cfg, store, tmp_path):
    _setup(store, tmp_path)
    (tmp_path / "arch").mkdir()
    text = await Cloud(store, Engine(cfg, store, None).archive).test()
    assert "works" in text and not [p for p in (tmp_path / "cloud").rglob("*") if p.is_file()]
    shutil.rmtree(tmp_path / "cloud", ignore_errors=True)
