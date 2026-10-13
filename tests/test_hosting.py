from pathlib import Path

from backuper.models import SourceRecord
from backuper.sources.base import RunContext
from backuper.sources.hosting_common import download_and_delete
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
    fs = FakeFS(["backup-old.tar.gz", "backup-new.tar.gz", "other.txt"])
    art = await download_and_delete(fs, "/", "backup-new.tar.gz", _ctx(store, tmp_path))
    assert set(fs.files) == {"backup-old.tar.gz", "other.txt"}
    assert art.entries[0].arcname == "backup-new.tar.gz" and art.entries[0].compress is False


async def test_keep_on_host_keeps_newest_and_removes_older_archives(store, tmp_path):
    fs = FakeFS(["backup-9.26.tar.gz", "backup-10.10.tar.gz", "backup-new.tar.gz", "public_html"])
    ctx = _ctx(store, tmp_path)
    await download_and_delete(fs, "/", "backup-new.tar.gz", ctx, keep_on_host=True)
    assert set(fs.files) == {"backup-new.tar.gz", "public_html"}
    assert len(ctx.notes) == 2 and not ctx.warnings
