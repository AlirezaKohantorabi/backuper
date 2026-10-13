"""Every run gets its own detailed log; every line says which run and source it belongs to."""

import logging

from backuper import logctx
from backuper.engine import Engine
from backuper.sources import REGISTRY
from tests.test_engine import FakeSender, FakeSource


async def test_run_log_has_tagged_steps_of_that_run_only(cfg, store, tmp_path, monkeypatch):
    monkeypatch.setitem(REGISTRY, "fake", FakeSource)
    logctx.install()
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        store.set_setting("archive_path", str(tmp_path / "arch"))
        store.create_source("DB", "fake", {"sizes": [1000]}, set())
        store.create_source("Broken", "fake", {"sizes": [], "fail": 1}, set())
        engine = Engine(cfg, store, FakeSender())
        logging.getLogger("backuper.outside").info("not part of any run")
        run_id = await engine.run(trigger="t")
        text = logctx.run_log_path(cfg.log_dir, run_id).read_text()
    finally:
        root.setLevel(old_level)
    assert f"[run#{run_id} DB]" in text and f"[run#{run_id} Broken]" in text   # tagged per source
    for step in ("started", "collecting", "zipping", "zipped", "archived", "uploading", "result DB: ok",
                 "FAILED: server unreachable", "finished"):
        assert step in text, step
    assert "not part of any run" not in text                                  # only this run's lines
    assert not any(isinstance(h, logctx.RunFileHandler) for h in root.handlers)  # handler removed


def test_prune_run_logs(cfg):
    d = cfg.log_dir / "runs"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (d / f"run-{i}.log").write_text("x")
    logctx.prune_run_logs(cfg.log_dir, keep=2)
    assert len(list(d.glob("run-*.log"))) == 2
