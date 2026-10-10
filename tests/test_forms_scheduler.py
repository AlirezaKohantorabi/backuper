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


def test_scheduler_plan_groups_by_cron(cfg, store):
    store.set_setting("schedule", "0 3 * * *")
    a = store.create_source("A", "smb_folder", {"path": r"\\h\s", "credential": "c"}, set())
    b = store.create_source("B", "smb_folder", {"path": r"\\h\s", "credential": "c", "schedule": "0 4 * * 5"}, set())
    c = store.create_source("C", "smb_folder", {"path": r"\\h\s", "credential": "c"}, set(), enabled=False)
    sched = Scheduler(Engine(cfg, store, None), store, cfg.timezone)
    plan = sched._plan()
    assert plan == {"0 3 * * *": [a], "0 4 * * 5": [b]}
    assert c not in sum(plan.values(), [])
    sched.reload()
    assert len(sched.next_runs()) == 2
