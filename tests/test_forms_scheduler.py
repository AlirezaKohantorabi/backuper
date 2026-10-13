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
