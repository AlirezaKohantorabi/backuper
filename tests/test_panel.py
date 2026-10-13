"""Drives the admin panel with fake Telegram events to catch UI-flow bugs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon import events

from backuper.bot.panel import Panel
from backuper.engine import Engine
from backuper.scheduler import Scheduler

OWNER = 42


class FakeMsg:
    def __init__(self, client, chat_id, text, buttons=None):
        self.client, self.chat_id, self.text, self.buttons = client, chat_id, text, buttons
        self.id = len(client.sent) + 1

    async def edit(self, text, **kw):
        self.text, self.buttons = text, kw.get("buttons")


class FakeClient:
    def __init__(self):
        self.sent: list[FakeMsg] = []

    async def send_message(self, chat, text, **kw):
        m = FakeMsg(self, chat, text, kw.get("buttons"))
        self.sent.append(m)
        return m

    async def edit_message(self, chat, msg_id, text, **kw):
        return None

    async def send_file(self, chat, path, **kw):
        self.files = getattr(self, "files", []) + [path]

    def add_event_handler(self, *a, **kw):
        pass


class FakeCB(events.CallbackQuery.Event):
    data = sender_id = chat_id = message_id = None  # shadow Telethon properties

    def __init__(self, data: str, uid: int = OWNER):
        self.data, self.sender_id, self.chat_id, self.message_id = data.encode(), uid, uid, 1
        self.text, self.buttons, self.alerts = None, None, []

    async def edit(self, text, buttons=None, **kw):
        self.text, self.buttons = text, buttons

    async def answer(self, message=None, alert=False, **kw):
        if alert:
            self.alerts.append(message)

    async def get_sender(self):
        return SimpleNamespace(first_name="Tester")


class FakeText:
    is_private = True

    def __init__(self, text: str, uid: int = OWNER):
        self.raw_text, self.sender_id, self.chat_id = text, uid, uid
        self.message = SimpleNamespace(reply_to=None)
        self.responses: list[str] = []
        self.deleted = False

    async def respond(self, text, **kw):
        self.responses.append(text)
        return SimpleNamespace(id=99)

    async def delete(self):
        self.deleted = True


@pytest.fixture
def panel(cfg, store):
    app = SimpleNamespace(cfg=cfg, store=store, client=FakeClient())
    app.engine = Engine(cfg, store, None)
    app.scheduler = Scheduler(app.engine, store, cfg.timezone)
    app.sender = None
    app.is_admin = lambda uid: uid == OWNER
    p = Panel(app)
    return p


def _data(b) -> str:
    # Telethon >= 1.45 wraps callback data in b.type; older versions expose b.data
    return (getattr(b, "data", None) or b.type.data).decode()


def all_buttons(markup) -> list[str]:
    return [_data(b) for row in (markup or []) for b in row]


async def press(panel, data):
    ev = FakeCB(data)
    await panel.on_callback(ev)
    assert not [a for a in ev.alerts if a and a.startswith("Error")], ev.alerts
    return ev


async def say(panel, text, uid=OWNER):
    ev = FakeText(text, uid)
    await panel.on_message(ev)
    return ev


async def test_full_admin_flow(panel, store):
    ev = await say(panel, "/start")
    assert "admin panel" in ev.responses[0]

    # non-admins are refused but told their ID
    ev = await say(panel, "/start", uid=7)
    assert "<code>7</code>" in ev.responses[0]

    # create a credential through the form
    await press(panel, "ca")
    await say(panel, "sql-share")
    await say(panel, "CORP\\svc")
    pw = await say(panel, "Sup3r!")
    assert pw.deleted  # password message removed from the chat
    await press(panel, "f:def")  # note -> default
    cred = store.get_credential_by_name("sql-share")
    assert cred.username == "CORP\\svc" and cred.password == "Sup3r!"

    # add an MSSQL collect source
    await press(panel, "sa")
    await press(panel, "sat:mssql_collect")
    await say(panel, "Hamrahan")
    bad = await say(panel, "D:\\not-unc")
    assert "⚠️" in bad.responses[0]
    await say(panel, "//172.16.3.251/SQLBackup")
    await press(panel, "f:c:0")       # credential button
    await press(panel, "f:def")       # patterns
    await press(panel, "f:v:0")       # one backup per: pattern
    await press(panel, "f:no")        # recursive
    await say(panel, "7")             # keep_last
    await say(panel, "26")            # max_age
    ev = await press(panel, "f:clr")  # group: none yet -> asks for its own time
    assert "Own time" in ev.text
    await say(panel, "19:00")
    ev = await press(panel, "sa")     # (any button is ignored while the summary waits)
    src_form = panel.forms.get(OWNER)
    assert src_form is None or src_form.kind == "source_add"
    await press(panel, "f:save")
    src = store.get_source_by_name("Hamrahan")
    assert src.config["share"] == r"\\172.16.3.251\SQLBackup" and src.config["keep_last"] == 7
    assert src.config["group"] == "" and src.config["schedule"] == "0 19 * * *"
    assert src.config["telegram"] is True  # advanced field got its default

    # view / edit a field / toggle / duplicate / rename
    ev = await press(panel, f"sv:{src.id}")
    assert "Hamrahan" in ev.text and f"sr:{src.id}" in all_buttons(ev.buttons)
    ev = await press(panel, f"se:{src.id}")
    keep_idx = next(_data(b) for row in ev.buttons for b in row
                    if _data(b).startswith("sef:") and b.text.startswith("Keep"))
    await press(panel, keep_idx)
    await say(panel, "20")
    assert store.get_source(src.id).config["keep_last"] == 20
    await press(panel, f"sx:{src.id}")
    assert not store.get_source(src.id).enabled
    await press(panel, f"sdup:{src.id}")
    assert store.get_source_by_name("Hamrahan copy")
    await press(panel, f"sen:{src.id}")
    await say(panel, "Hamrahan copy")  # duplicate name rejected
    await say(panel, "Hamrahan-DB")
    assert store.get_source(src.id).name == "Hamrahan-DB"

    # nested "new credential" from inside a source form
    await press(panel, "sat:mikrotik")
    await say(panel, "Center-Core")
    await say(panel, "192.168.10.254")
    await press(panel, "f:def")       # port
    await press(panel, "f:newcred")
    await say(panel, "mikrotik-bot")
    await say(panel, "backuper")
    await say(panel, "pw")
    await press(panel, "f:def")       # note -> back in parent form, credential filled in
    assert panel.forms[OWNER].values["credential"] == "mikrotik-bot"

    # cancel returns cleanly
    await press(panel, "f:cancel")
    assert OWNER not in panel.forms

    # groups: create one at 08:00, put Hamrahan in it, see it in the schedule
    await press(panel, "gl")
    await press(panel, "ga")
    await say(panel, "Daily")
    await say(panel, "08:00")
    bad = await say(panel, "8 o'clock")            # upload-from must be HH:MM
    assert "HH:MM" in bad.responses[0]
    await press(panel, "f:clr")                    # no upload-from time
    group = store.get_group_by_name("Daily")
    assert group.schedule == "0 8 * * *" and group.upload_at == ""
    await press(panel, f"ge:{group.id}:upload_at")
    await say(panel, "9:15")
    assert store.get_group(group.id).upload_at == "09:15"
    await press(panel, f"gmt:{group.id}:{src.id}")
    assert store.get_source(src.id).config["group"] == "Daily"
    ev = await press(panel, f"gv:{group.id}")
    assert f"sv:{src.id}" in all_buttons(ev.buttons)         # members are buttons to their pages
    ev = await press(panel, f"sv:{src.id}")
    assert "Group <b>Daily</b>" in ev.text
    # move it out of the group from its own page, then back
    await press(panel, f"sg:{src.id}")
    await press(panel, f"sgs:{src.id}:0")
    assert store.get_source(src.id).config["group"] == ""
    await press(panel, f"sgs:{src.id}:{group.id}")
    assert store.get_source(src.id).config["group"] == "Daily"
    # one-time run: bad input is refused, a good one is stored and can be cancelled
    await press(panel, f"so:{src.id}")
    bad = await say(panel, "yesterday")
    assert "use 14:30" in bad.responses[0]
    await say(panel, "+2h")
    once = store.list_one_offs()
    assert len(once) == 1 and once[0].source_ids == [src.id]
    ev = await press(panel, "sch")
    assert "One-time runs" in ev.text and f"oc:{once[0].id}" in all_buttons(ev.buttons)
    await press(panel, f"oc:{once[0].id}")
    assert not store.list_one_offs()
    await press(panel, f"ge:{group.id}:schedule")
    await say(panel, "07:30")
    assert store.get_group(group.id).schedule == "30 7 * * *"
    assert not panel.app.scheduler.plan()          # its only source was disabled above
    await press(panel, f"sx:{src.id}")             # enable it again
    plan = panel.app.scheduler.plan()
    assert [j.source_ids for j in plan.values() if j.label == "group Daily"] == [[src.id]]
    await press(panel, f"ge:{group.id}:name")
    await say(panel, "Morning")
    assert store.get_source(src.id).config["group"] == "Morning"   # members follow a rename

    # settings
    await press(panel, "set")
    await press(panel, "setk:zip_password")
    await say(panel, "zip-pw")
    assert store.get_setting("zip_password") == "zip-pw"
    await press(panel, "setk:topic_mode")
    await press(panel, "f:v:1")
    assert store.get_setting("topic_mode") == "source"
    ev = await press(panel, "sch")
    assert "Morning" in ev.text and "daily 07:30" in ev.text

    # admins (owner only)
    await press(panel, "aa")
    await say(panel, "555")
    await say(panel, "Ali")
    assert 555 in store.admin_ids()
    await press(panel, "ad:555")
    assert 555 not in store.admin_ids()

    # credential in use can't be deleted
    ev = await press(panel, f"cd:{cred.id}")
    assert ev.alerts and "Still used" in ev.alerts[0]

    # history: a run's detailed log can be fetched
    from backuper import logctx
    run_id = store.create_run("t", "1405-07-21")
    ev = await press(panel, f"hv:{run_id}")
    assert f"hlog:{run_id}" in all_buttons(ev.buttons)
    ev = await press(panel, f"hlog:{run_id}")
    assert ev.alerts and "No log" in ev.alerts[0]              # nothing kept yet → clear message
    path = logctx.run_log_path(panel.app.cfg.log_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("log")
    await press(panel, f"hlog:{run_id}")
    assert panel.app.client.files == [str(path)]

    # history + delete
    await press(panel, "hl")
    await press(panel, f"sd:{src.id}")
    await press(panel, f"sdy:{src.id}")
    assert store.get_source(src.id) is None
    await press(panel, "m")
