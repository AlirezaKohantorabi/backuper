"""The "This Backuper server" source: the bot's own database, .env and local/ configuration."""

from __future__ import annotations

import dataclasses
import sqlite3

import pyzipper

from backuper.engine import Engine
from backuper.models import OK
from backuper.sources import self_backup


async def test_bot_backs_up_itself(cfg, store, tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    (app_dir / "local").mkdir(parents=True)
    (app_dir / "local" / "company.yaml").write_text("sources: []\n")
    env = app_dir / ".env"
    env.write_text("SECRET_KEY=abc\n")
    monkeypatch.setattr(self_backup, "APP_DIR", app_dir)
    cfg = dataclasses.replace(cfg, env_file=env)
    arch = tmp_path / "arch"
    store.set_setting("archive_path", str(arch))
    store.set_setting("zip_password", "pw")
    store.create_source("Backuper", "self", {"telegram": False}, set())

    run_id = await Engine(cfg, store, None).run(trigger="t")
    item = store.run_items(run_id)[0]
    assert item["status"] == OK and '"verified": 4' in item["files"]
    zip_path = next(arch.rglob("Backuper_*.zip"))
    with pyzipper.AESZipFile(zip_path) as z:
        z.setpassword(b"pw")
        assert sorted(z.namelist()) == [".env", "RESTORE.txt", "data/backuper.db", "local/company.yaml"]
        assert z.read(".env") == b"SECRET_KEY=abc\n"
        db = tmp_path / "restored.db"
        db.write_bytes(z.read("data/backuper.db"))
    names = [r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sources")]
    assert names == ["Backuper"]                     # a consistent, readable copy of the live database
    assert not list(cfg.staging_dir.rglob("backuper.db"))  # the temporary copy is gone


async def test_self_backup_test_lists_what_it_takes(cfg, store, tmp_path, monkeypatch):
    from backuper import jalali
    from backuper.sources import RunContext, get_type

    monkeypatch.setattr(self_backup, "APP_DIR", tmp_path)
    env = tmp_path / ".env"
    env.write_text("x")
    src_id = store.create_source("Backuper", "self", {}, set())
    src = store.get_source(src_id)
    ctx = RunContext(store, src, tmp_path / "w", jalali.now_jalali(cfg.timezone),
                     app=dataclasses.replace(cfg, env_file=env))
    text = await get_type("self")(src, ctx).test()
    assert "data/backuper.db" in text and ".env" in text
