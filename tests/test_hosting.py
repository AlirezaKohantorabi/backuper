from pathlib import Path

from backuper.models import SourceRecord
from backuper.sources.base import RunContext
from backuper.sources.hosting_common import clear_old_archives, companions, download_and_delete, keep_mode
from backuper.transport.remote import RemoteEntry, RemoteFS


class FakeFS(RemoteFS):
    def __init__(self, names):
        self.files = {n: b"x" * 10 for n in names}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def listdir(self, path):
        return [RemoteEntry(name=n, path=f"/{n}", size=len(d), is_dir=False) for n, d in self.files.items()]

    async def download(self, path, local: Path):
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[path.lstrip("/")])

    async def remove(self, path):
        del self.files[path.lstrip("/")]


def _ctx(store, tmp_path):
    src = SourceRecord(id=1, name="Site", type="cpanel", enabled=True, config={})
    import jdatetime
    return RunContext(store, src, tmp_path / "w", jdatetime.datetime(1405, 7, 21, 8, 0))


async def test_default_deletes_only_the_new_backup(store, tmp_path):
    fs = FakeFS(["backup-old.tar.gz", "backup-new.tar.gz", "backup-new.json", "other.txt"])
    art = await download_and_delete(fs, "/", "backup-new.tar.gz", _ctx(store, tmp_path))
    assert set(fs.files) == {"backup-old.tar.gz", "other.txt"}           # DirectAdmin's .json goes too
    assert art.entries[0].arcname == "backup-new.tar.gz" and art.entries[0].compress is False


async def test_keep_on_host_keeps_newest_and_removes_older_archives(store, tmp_path):
    fs = FakeFS(["backup-9.26.tar.gz", "backup-10.10.tar.gz", "backup-new.tar.gz", "public_html"])
    ctx = _ctx(store, tmp_path)
    await download_and_delete(fs, "/", "backup-new.tar.gz", ctx, keep_on_host=True)
    assert set(fs.files) == {"backup-new.tar.gz", "public_html"}
    assert len(ctx.notes) == 2 and not ctx.warnings


async def test_replace_mode_clears_old_backups_before_the_new_one(store, tmp_path):
    fs = FakeFS(["backup-2026-10-10.tar.gz", "backup-2026-10-10.json", "domains"])
    ctx = _ctx(store, tmp_path)
    assert await clear_old_archives(fs, "/", ctx) == 1
    assert set(fs.files) == {"domains"} and ctx.notes


def test_keep_modes_and_side_files():
    assert keep_mode({}) == "no" and keep_mode({"keep_on_host": False}) == "no"
    assert keep_mode({"keep_on_host": True}) == "newest"            # old yes/no configs
    assert keep_mode({"keep_on_host": "replace"}) == "replace"
    assert companions("backup-x_2026-10-10-09-16-01.tar.gz") == ["backup-x_2026-10-10-09-16-01.json"]
