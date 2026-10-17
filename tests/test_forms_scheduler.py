from datetime import datetime

from backuper.bot.forms import FormState, prompt
from backuper.engine import Engine
from backuper.scheduler import Scheduler
from backuper.sources import get_type


def test_form_skips_hidden_fields():
    cls = get_type("remote_folder")
    st = FormState("source_add", "t", [f for f in cls.fields if "advanced" not in f.tags])
    st.accept("sftp")                      # protocol
    st.accept("10.0.0.1")                  # host
    st.use_default()                       # port
    st.accept("cred")                      # credential
    st.accept("/data")                     # remote_path
    st.use_default()                       # exclude
    st.accept("no")                        # delete_after
    assert st.current_field() is None      # verify_tls only shown for ftps

    st2 = FormState("source_add", "t", list(cls.fields))
    st2.accept("ftps")
    for _ in range(6):
        st2.use_default() if st2.current_field().default is not None else st2.accept("x")
    assert st2.current_field().key == "verify_tls"
    text, buttons = prompt(st2, [])
    assert "Verify TLS" in text and len(buttons) >= 2


def test_scheduler_plan_groups_and_own_times(cfg, store):
    store.save_group("Daily", "0 8 * * *")
    store.save_group("Paused", "0 9 * * *", enabled=False)
    a = store.create_source("A", "smb_folder", {"path": r"\\h\s", "credential": "c", "group": "Daily"}, set())
    a2 = store.create_source("A2", "smb_folder", {"path": r"\\h\s", "credential": "c", "group": "daily"}, set())
    b = store.create_source("B", "smb_folder", {"path": r"\\h\s", "credential": "c", "schedule": "0 19 * * *"}, set())
    store.create_source("C", "smb_folder", {"path": r"\\h\s", "credential": "c", "group": "Daily"}, set(),
                        enabled=False)
    store.create_source("D", "smb_folder", {"path": r"\\h\s", "credential": "c"}, set())          # manual only
    store.create_source("E", "smb_folder", {"path": r"\\h\s", "credential": "c", "group": "Paused",
                                            "schedule": "0 5 * * *"}, set())  # disabled group: not scheduled
    sched = Scheduler(Engine(cfg, store, None), store, cfg.timezone)
    plan = {j.label: j for j in sched.plan().values()}
    assert set(plan) == {"group Daily", "B"}
    assert sorted(plan["group Daily"].source_ids) == sorted([a, a2])
    assert plan["B"].source_ids == [b] and plan["B"].cron == "0 19 * * *"
    sched.reload()
    assert [label for _, label, _ in sched.next_runs()] and len(sched.next_runs()) == 2


def test_legacy_global_schedule_becomes_a_group(cfg, tmp_path):
    from backuper.crypto import SecretBox
    from backuper.db import Store

    path = tmp_path / "old.db"
    box = SecretBox(cfg.secret_key)
    old = Store(path, box)
    old.set_json("migrated_groups", False)
    old.set_setting("schedule", "0 3 * * *")
    old.create_source("A", "smb_folder", {"path": r"\\h\s", "credential": "c"}, set())
    old.create_source("B", "smb_folder", {"path": r"\\h\s", "credential": "c", "schedule": "0 4 * * *"}, set())
    old.close()
    new = Store(path, box)           # reopening runs the one-time migration
    g = new.get_group_by_name("Daily")
    assert g and g.schedule == "0 3 * * *"
    assert new.get_source_by_name("A").config["group"] == "Daily"
    assert not new.get_source_by_name("B").config.get("group")


def test_one_off_runs_are_scheduled_or_marked_missed(cfg, store):
    from datetime import datetime, timedelta

    sid = store.create_source("A", "smb_folder", {"path": r"\\h\s", "credential": "c"}, set())
    now = datetime.now(cfg.timezone)
    future = store.add_one_off("A", [sid], now + timedelta(hours=3), 42)
    recent = store.add_one_off("A", [sid], now - timedelta(minutes=10), 42)   # missed briefly: still runs
    old = store.add_one_off("A", [sid], now - timedelta(hours=5), 42)         # too late: missed
    sched = Scheduler(Engine(cfg, store, None), store, cfg.timezone)
    sched.reload()
    ids = {j.id for j in sched._sched.get_jobs()}
    assert f"once:{future}" in ids and f"once:{recent}" in ids and f"once:{old}" not in ids
    assert store.get_one_off(old).status == "missed"
    assert any(label.startswith("⏱ once") for _, label, _ in sched.next_runs())


def test_failed_sources_are_retried_later(cfg, store):
    from backuper.models import FAILED, OK

    a = store.create_source("A", "smb_folder", {}, set())
    b = store.create_source("B", "smb_folder", {}, set())
    run_id = store.create_run("group Daily", "1405-07-25")
    store.add_run_item(run_id, a, "A", FAILED, "No route to host", [], 0, 1)
    store.add_run_item(run_id, b, "B", OK, "", [], 0, 1)
    sched = Scheduler(Engine(cfg, store, None), store, cfg.timezone)

    oid = sched.retry_failed(run_id, 1)
    o = store.get_one_off(oid)
    assert o.source_ids == [a] and o.retry == 1 and "retry 1/3" in o.label   # only the failed source
    minutes = (o.run_at - datetime.now(cfg.timezone)).total_seconds() / 60
    assert 59 <= minutes <= 61                                                # default: an hour later
    assert sched.retry_failed(run_id, 4) is None                              # at most 3 tries
    store.set_setting("retry_failed_after", 0)
    assert sched.retry_failed(run_id, 1) is None                              # 0 = off


async def test_one_off_added_from_the_command_line_is_picked_up(cfg, store, monkeypatch, capsys):
    """`backuper once` writes to the database; the running service schedules (and cancels) it by itself."""
    from types import SimpleNamespace

    from backuper import cli

    src = store.create_source("Hamrahan-Manual", "mssql_trigger", {}, set())
    store.save_group("Daily", "0 8 * * *", True)
    store.create_source("Arpa", "mssql_trigger", {"group": "Daily"}, set())
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(cli, "open_store", lambda c: store)
    sched = Scheduler(Engine(cfg, store, None), store, cfg.timezone)
    sched.start()
    try:
        args = SimpleNamespace(env=None, when="+2h", names=["Hamrahan-Manual", "Daily"], list=False, cancel=None)
        assert cli.cmd_once(args) == 0
        o = store.list_one_offs()[0]
        assert len(o.source_ids) == 2 and src in o.source_ids              # a source + a group's members
        await sched._sync_one_offs()
        assert any(job_id == f"once:{o.id}" for job_id in (j.id for j in sched._sched.get_jobs()))
        assert cli.cmd_once(SimpleNamespace(env=None, when=None, names=[], list=False, cancel=o.id)) == 0
        await sched._sync_one_offs()
        assert not any(j.id == f"once:{o.id}" for j in sched._sched.get_jobs())
        bad = SimpleNamespace(env=None, when="22:00", names=["Nope"], list=False, cancel=None)
        assert cli.cmd_once(bad) == 2 and "No source or group" in capsys.readouterr().err
    finally:
        sched.shutdown()
