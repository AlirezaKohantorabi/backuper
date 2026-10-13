"""The live status file shows what's running (with progress) and is written locally and into the archive."""

import time

from backuper.engine import Engine, RunProgress, SourceProgress, progress_text
from backuper.status import STATUS_NAME, StatusWriter


def test_progress_text_has_percent_size_speed_and_time_left():
    text = progress_text(50 * 2**20, 200 * 2**20, 5.0)
    assert text.startswith("25%") and "50.0 MB / 200.0 MB" in text and "10.0 MB/s" in text and "left" in text
    assert progress_text(0, 0, 65) == "(1m 05s)"


def test_status_file_while_running_and_idle(cfg, store, tmp_path):
    store.set_setting("archive_path", str(tmp_path / "arch"))
    engine = Engine(cfg, store, None)
    writer = StatusWriter(cfg, store, engine)

    prog = RunProgress(run_id=7, trigger="group Files")
    sp = SourceProgress("Edari")
    sp.begin("copying → archive", 100 * 2**20)
    sp.phase_started = time.monotonic() - 10
    sp.done_bytes = 40 * 2**20
    prog.sources[1] = sp
    engine.runs[7] = prog
    assert writer.write_once()
    text = writer.local_path.read_text(encoding="utf-8")
    assert "RUNNING" in text and "Run #7" in text and "Edari: copying → archive 40%" in text and "left" in text
    assert (tmp_path / "arch" / STATUS_NAME).read_text(encoding="utf-8-sig") == text   # same file in the archive
    assert "<b>" not in text                                                            # plain text, no HTML

    engine.runs.clear()
    assert writer.write_once() and "Idle" in writer.local_path.read_text(encoding="utf-8")
    assert not writer.write_once()            # nothing changed → not rewritten
