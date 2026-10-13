"""The Telegram admin panel: menus, add/edit wizards, runs, history, settings."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import TYPE_CHECKING, Any

from telethon import Button, errors, events

from backuper import jalali
from backuper.bot.forms import FormState, prompt, summary
from backuper.destinations.telegram import TOPICS_KEY, esc
from backuper.engine import EngineBusy
from backuper.fields import CREDENTIAL, GROUP, INT, SCHEDULE, SECRET, TEXT, Field, FieldError, describe_schedule
from backuper.models import CATEGORY_TITLES, STATUS_ICON
from backuper.settings import SETTINGS_BY_KEY, SETTINGS_FIELDS
from backuper.sources import REGISTRY, RunContext, SourceError, credential_keys_by_type, get_type
from backuper.util import human_size, short_error

if TYPE_CHECKING:
    from backuper.bot.app import BotApp

log = logging.getLogger(__name__)

CAT_ICON = {"databases": "🗄", "network": "🌐", "websites": "🌍", "files": "📁"}
PAGE = 30
BACK_MAIN = [Button.inline("🏠 Main menu", b"m")]

NAME_FIELD = Field("name", "Name", TEXT, help="Shown in Telegram and used in file names, e.g. Hamrahan or Center-Core.")
CRED_FIELDS = [
    Field("name", "Credential name", TEXT, example="domain-backup",
          help="A label to pick this login from lists, e.g. domain-backup, mikrotik-bot, cpanel-site1."),
    Field("username", "Username", TEXT, example="CORP\\svc-backup",
          help="Windows/SMB: DOMAIN\\user or user. SQL: login name. cPanel token: the cPanel username."),
    Field("password", "Password / token", SECRET, help="Password, API token or DirectAdmin login key."),
    Field("note", "Note", TEXT, required=False, default="", help="Optional reminder (where it's used, who owns it)."),
]
GROUP_FIELDS = [
    Field("name", "Group name", TEXT, example="Daily", help="e.g. Daily, Night, Weekly."),
    Field("schedule", "Start time", SCHEDULE, example="08:00",
          help="When the group starts. All its sources are backed up, then their files go to Telegram smallest "
               "first, one at a time — no need to space anything out. Several times: 08:00, 20:00. "
               "Other days: cron lines like '0 8 * * 6' (Saturdays)."),
]
ADMIN_FIELDS = [
    Field("user_id", "Telegram user ID", INT, min=1, help="They can get it by sending /id to this bot."),
    Field("name", "Name", TEXT, required=False, default=""),
]

HELP = """<b>Backuper — how it works</b>

1. <b>Credentials</b>: logins (SMB, SQL, router, panel). Stored encrypted; reuse them across sources.
2. <b>Sources</b>: what to back up. Each source becomes one or more AES-256 zips per run.
3. <b>Schedule</b>: when the main run starts. All sources without their own schedule run together.
4. Each run: collect → zip → copy to the archive (<i>folder per Jalali date</i>) → upload to the group, <b>smallest file first</b> → delete old backups on the sources → report in the Reports topic.

<b>Commands</b>
/menu — this panel
/run — run everything now · /run Name — run one source
/status — progress of the current run
/cancel — abort the current form
/id — show your Telegram ID (and the chat/topic ID in groups)
/bind — (in the group) send backups to this group

Zips over the part size are sent as <code>.zip.001</code>, <code>.zip.002</code> … — download all parts into one folder and open the <code>.001</code> with 7-Zip."""


class Panel:
    def __init__(self, app: BotApp):
        self.app = app
        self.store = app.store
        self.forms: dict[int, FormState] = {}

    def register(self) -> None:
        self.app.client.add_event_handler(self.on_callback, events.CallbackQuery())
        self.app.client.add_event_handler(self.on_message, events.NewMessage(incoming=True))

    # ======================================================================
    # plumbing
    # ======================================================================
    COMMANDS = frozenset({"/start", "/menu", "/cancel", "/help", "/status", "/run", "/id", "/bind"})

    @classmethod
    def _cmd(cls, text: str) -> tuple[str | None, str]:
        """Split a bot command. Anything else (including paths like //server/share) is plain text."""
        head, _, rest = text.strip().partition(" ")
        name = head.split("@", 1)[0].lower()
        return (name, rest.strip()) if name in cls.COMMANDS else (None, text)

    async def _respond(self, event, text: str, buttons=None) -> None:
        """Edit the message behind a button press, or send a new message."""
        if isinstance(event, events.CallbackQuery.Event):
            try:
                await event.edit(text, buttons=buttons, parse_mode="html", link_preview=False)
            except errors.MessageNotModifiedError:
                pass
        else:
            await event.respond(text, buttons=buttons, parse_mode="html", link_preview=False)

    async def on_message(self, event) -> None:
        text = event.raw_text or ""
        cmd, arg = self._cmd(text)
        uid = event.sender_id

        if not event.is_private:
            if cmd == "/id":
                topic = getattr(event.message.reply_to, "reply_to_top_id", None) or \
                    getattr(event.message.reply_to, "reply_to_msg_id", None)
                await event.reply(f"Chat ID: <code>{event.chat_id}</code>" +
                                  (f"\nTopic ID: <code>{topic}</code>" if topic else ""), parse_mode="html")
            elif cmd == "/bind" and self.app.is_admin(uid):
                await self.bind_group(event)
            return

        if cmd == "/id":
            await event.respond(f"Your Telegram ID: <code>{uid}</code>", parse_mode="html")
            return
        if not self.app.is_admin(uid):
            if cmd == "/start":
                await event.respond(f"⛔ You're not an admin of this bot.\nYour Telegram ID is <code>{uid}</code> — "
                                    "send it to an admin if you need access.", parse_mode="html")
            return

        if cmd in ("/start", "/menu"):
            self.forms.pop(uid, None)
            await self.show_main(event)
        elif cmd == "/cancel":
            self.forms.pop(uid, None)
            await event.respond("Cancelled.", buttons=[BACK_MAIN])
        elif cmd == "/help":
            await event.respond(HELP, parse_mode="html", buttons=[BACK_MAIN])
        elif cmd == "/status":
            await self.show_status(event)
        elif cmd == "/run":
            if arg:
                src = self.store.get_source_by_name(arg)
                if not src:
                    await event.respond(f"No source named <b>{esc(arg)}</b>.", parse_mode="html")
                    return
                await self.start_run(event, [src.id], src.name)
            else:
                await self.start_run(event, None, "all sources")
        elif uid in self.forms and cmd is None:
            await self.form_text(event, self.forms[uid], text)
        else:
            await self.show_main(event)

    async def on_callback(self, event) -> None:
        if not self.app.is_admin(event.sender_id):
            await event.answer("⛔ Not authorized", alert=True)
            return
        data = event.data.decode(errors="replace")
        head, _, rest = data.partition(":")
        handler = self._routes().get(head)
        if handler is None:
            await event.answer()
            return
        try:
            await handler(event, rest)
            try:
                await event.answer()
            except errors.QueryIdInvalidError:
                pass
        except (FieldError, SourceError, EngineBusy) as e:
            await event.answer(str(e)[:190], alert=True)
        except Exception as e:  # noqa: BLE001
            log.exception("callback %s failed", data)
            await event.answer(f"Error: {short_error(e)}"[:190], alert=True)

    def _routes(self) -> dict[str, Any]:
        return {
            "m": lambda e, _: self.show_main(e),
            "help": lambda e, _: self._respond(e, HELP, [BACK_MAIN]),
            "sl": lambda e, a: self.show_sources(e, int(a or 0)),
            "sv": lambda e, a: self.show_source(e, int(a)),
            "sa": lambda e, _: self.choose_type(e),
            "sat": lambda e, a: self.start_add_source(e, a),
            "se": lambda e, a: self.show_source_edit(e, int(a)),
            "sef": self.edit_source_field,
            "sen": lambda e, a: self.rename_source(e, int(a)),
            "sx": lambda e, a: self.toggle_source(e, int(a)),
            "sd": lambda e, a: self.confirm_delete_source(e, int(a)),
            "sdy": lambda e, a: self.delete_source(e, int(a)),
            "sdup": lambda e, a: self.duplicate_source(e, int(a)),
            "sk": lambda e, a: self.forget_keys(e, int(a)),
            "sr": lambda e, a: self.run_source(e, int(a)),
            "st": lambda e, a: self.test_source(e, int(a)),
            "cl": lambda e, _: self.show_credentials(e),
            "cv": lambda e, a: self.show_credential(e, int(a)),
            "ca": lambda e, _: self.start_form(e, FormState("cred_add", "➕ New credential", CRED_FIELDS)),
            "cef": self.edit_credential_field,
            "cd": lambda e, a: self.confirm_delete_credential(e, int(a)),
            "cdy": lambda e, a: self.delete_credential(e, int(a)),
            "ra": lambda e, _: self.start_run(e, None, "all sources"),
            "hl": lambda e, _: self.show_history(e),
            "hv": lambda e, a: self.show_run(e, int(a)),
            "hr": lambda e, a: self.resend(e, int(a)),
            "sch": lambda e, _: self.show_schedule(e),
            "gl": lambda e, _: self.show_groups(e),
            "gv": lambda e, a: self.show_group(e, int(a)),
            "ga": lambda e, _: self.start_form(e, FormState("group_add", "🗂 New group", GROUP_FIELDS)),
            "ge": self.edit_group_field,
            "gm": lambda e, a: self.show_group_members(e, int(a)),
            "gmt": self.toggle_group_member,
            "gr": lambda e, a: self.run_group(e, int(a)),
            "gx": lambda e, a: self.toggle_group(e, int(a)),
            "gd": lambda e, a: self.confirm_delete_group(e, int(a)),
            "gdy": lambda e, a: self.delete_group(e, int(a)),
            "set": lambda e, _: self.show_settings(e),
            "setk": lambda e, a: self.edit_setting(e, a),
            "seta": lambda e, _: self.test_archive(e),
            "sett": lambda e, _: self.reset_topics(e),
            "al": lambda e, _: self.show_admins(e),
            "aa": lambda e, _: self.add_admin(e),
            "ad": lambda e, a: self.remove_admin(e, int(a)),
            "stat": lambda e, _: self.show_status(e),
            "f": self.form_button,
            "noop": lambda e, _: asyncio.sleep(0),
        }

    # ======================================================================
    # main menu / status
    # ======================================================================
    async def show_main(self, event) -> None:
        sources = self.store.list_sources()
        enabled = sum(1 for s in sources if s.enabled)
        runs = self.store.list_runs(1)
        lines = ["🛡 <b>Backuper</b> — admin panel", ""]
        lines.append(f"📦 Sources: {len(sources)} ({enabled} enabled)")
        nxt = self.app.scheduler.next_runs()
        if nxt:
            when = jalali.to_jalali(nxt[0][0].astimezone(self.app.cfg.timezone))
            lines.append(f"⏰ Next run: {jalali.pretty(when)}")
        else:
            lines.append("⏰ Next run: — (no schedule)")
        if runs:
            r = runs[0]
            lines.append(f"🕘 Last run: {STATUS_ICON.get(r['status'], '•')} #{r['id']} {r['jdate']} "
                         f"({human_size(r['total_bytes'])})")
        if self.app.engine.busy:
            lines.append(f"⏳ <b>{len(self.app.engine.runs)} run(s) in progress</b> — /status")
        warn = []
        if not self.store.get_setting("chat_id"):
            warn.append("no Telegram group yet — add me to the group as admin and send /bind there")
        if not self.store.get_setting("zip_password"):
            warn.append("zip password not set — zips are NOT encrypted (Settings)")
        if not self.store.get_setting("archive_path"):
            warn.append("no archive location — zips only go to Telegram (Settings)")
        if warn:
            lines += [""] + [f"⚠️ {w}" for w in warn]
        buttons = [
            [Button.inline("📦 Sources", b"sl:0"), Button.inline("🔑 Credentials", b"cl")],
            [Button.inline("▶️ Run all now", b"ra"), Button.inline("📊 History", b"hl")],
            [Button.inline("🗂 Groups", b"gl"), Button.inline("⏰ Schedule", b"sch")],
            [Button.inline("⚙️ Settings", b"set")],
            [Button.inline("👥 Admins", b"al"), Button.inline("❓ Help", b"help")],
        ]
        await self._respond(event, "\n".join(lines), buttons)

    async def show_status(self, event) -> None:
        if self.app.engine.busy:
            text = self.app.engine.render_status()
        else:
            runs = self.store.list_runs(1)
            text = (runs[0]["summary"] or "No runs yet.") if runs else "No runs yet."
        await self._respond(event, text, [[Button.inline("🔄 Refresh", b"stat")], BACK_MAIN])

    # ======================================================================
    # sources
    # ======================================================================
    async def show_sources(self, event, page: int = 0) -> None:
        sources = self.store.list_sources()
        chunk = sources[page * PAGE:(page + 1) * PAGE]
        rows, row = [], []
        for s in chunk:
            cls = REGISTRY.get(s.type)
            icon = CAT_ICON.get(cls.category, "📦") if cls else "❓"
            row.append(Button.inline(f"{'' if s.enabled else '⏸ '}{icon} {s.name}", f"sv:{s.id}".encode()))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        nav = []
        if page > 0:
            nav.append(Button.inline("◀️", f"sl:{page - 1}".encode()))
        if (page + 1) * PAGE < len(sources):
            nav.append(Button.inline("▶️", f"sl:{page + 1}".encode()))
        if nav:
            rows.append(nav)
        rows.append([Button.inline("➕ Add source", b"sa"), *BACK_MAIN])
        text = f"📦 <b>Sources</b> ({len(sources)})" + ("" if sources else "\n\nNothing yet — add your first source.")
        await self._respond(event, text, rows)

    def _source_text(self, src) -> str:
        cls = REGISTRY.get(src.type)
        if cls is None:
            return f"❓ <b>{esc(src.name)}</b> — unknown type {esc(src.type)}"
        lines = [f"{CAT_ICON.get(cls.category, '📦')} <b>{esc(src.name)}</b> — "
                 f"{'✅ enabled' if src.enabled else '⏸ disabled'}", f"<i>{esc(cls.title)}</i>", ""]
        for f in cls.all_fields():
            if f.visible(src.config) and f.key in src.config and src.config[f.key] not in ("", None, []):
                lines.append(f"• {esc(f.label)}: <code>{esc(f.display(src.config[f.key]))}</code>")
        if src.state.get("host_key") or src.state.get("transfer_host_key"):
            lines.append("• 🔐 SSH host key pinned")
        last = self.store.last_item_for_source(src.id)
        if last:
            lines += ["", f"Last: {STATUS_ICON.get(last['status'], '•')} run #{last['run_id']} · {last['jdate']} · "
                          f"{human_size(last['bytes'])}"]
            if last["message"]:
                lines.append(f"<i>{esc(last['message'][:300])}</i>")
        return "\n".join(lines)

    async def show_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src is None:
            await self.show_sources(event)
            return
        rows = [
            [Button.inline("▶️ Run now", f"sr:{sid}".encode()), Button.inline("🧪 Test", f"st:{sid}".encode())],
            [Button.inline("✏️ Edit", f"se:{sid}".encode()),
             Button.inline("⏸ Disable" if src.enabled else "▶️ Enable", f"sx:{sid}".encode())],
            [Button.inline("📄 Duplicate", f"sdup:{sid}".encode()), Button.inline("🗑 Delete", f"sd:{sid}".encode())],
        ]
        if src.state.get("host_key") or src.state.get("transfer_host_key"):
            rows.append([Button.inline("🔓 Forget pinned SSH key", f"sk:{sid}".encode())])
        rows.append([Button.inline("⬅️ Sources", b"sl:0"), *BACK_MAIN])
        await self._respond(event, self._source_text(src), rows)

    async def choose_type(self, event) -> None:
        rows = [[Button.inline(f"{CAT_ICON.get(cls.category, '📦')} {cls.title}", f"sat:{tid}".encode())]
                for tid, cls in REGISTRY.items()]
        rows.append([Button.inline("⬅️ Back", b"sl:0")])
        text = "➕ <b>Add source</b> — what kind?\n\n" + "\n\n".join(
            f"{CAT_ICON.get(c.category, '📦')} <b>{esc(c.title)}</b>\n{esc(c.description)}" for c in REGISTRY.values())
        await self._respond(event, text[:4000], rows)

    async def start_add_source(self, event, type_id: str) -> None:
        cls = get_type(type_id)
        fields = [NAME_FIELD, *[f for f in cls.all_fields() if "advanced" not in f.tags]]
        await self.start_form(event, FormState("source_add", f"➕ {cls.title}", fields, type_id=type_id))

    async def show_source_edit(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src is None:
            return
        cls = get_type(src.type)
        rows = [[Button.inline(f"🏷 Name: {src.name}"[:60], f"sen:{sid}".encode())]]
        for i, f in enumerate(cls.all_fields()):
            if not f.visible(src.config):
                continue
            label = f"{f.label}: {f.display(src.config.get(f.key))}"
            rows.append([Button.inline(label[:60], f"sef:{sid}:{i}".encode())])
        rows.append([Button.inline("✅ Done", f"sv:{sid}".encode())])
        await self._respond(event, f"✏️ <b>Edit {esc(src.name)}</b> — pick a field:", rows)

    async def edit_source_field(self, event, arg: str) -> None:
        sid_s, idx_s = arg.split(":")
        src = self.store.get_source(int(sid_s))
        if src is None:
            return
        cls = get_type(src.type)
        f = cls.all_fields()[int(idx_s)]
        await self.start_form(event, FormState("source_edit", f"✏️ {src.name}", [f], current=dict(src.config),
                                               editing=True, target=src.id, type_id=src.type))

    async def rename_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src:
            await self.start_form(event, FormState("source_rename", f"🏷 Rename {src.name}", [NAME_FIELD],
                                                   current={"name": src.name}, editing=True, target=sid))

    async def toggle_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src:
            self.store.update_source(sid, enabled=not src.enabled)
            self.app.scheduler.reload()
            await self.show_source(event, sid)

    async def confirm_delete_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src:
            await self._respond(event, f"🗑 Delete <b>{esc(src.name)}</b>? Archived zips are not touched.",
                                [[Button.inline("Yes, delete", f"sdy:{sid}".encode()),
                                  Button.inline("No", f"sv:{sid}".encode())]])

    async def delete_source(self, event, sid: int) -> None:
        self.store.delete_source(sid)
        self.app.scheduler.reload()
        await self.show_sources(event)

    async def duplicate_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src is None:
            return
        name, n = f"{src.name} copy", 2
        while self.store.get_source_by_name(name):
            name, n = f"{src.name} copy {n}", n + 1
        cls = get_type(src.type)
        new_id = self.store.create_source(name, src.type, dict(src.config), cls.secret_keys(), enabled=False)
        await event.answer("Copied (disabled). Rename it and change what differs.", alert=True)
        await self.show_source_edit(event, new_id)

    async def forget_keys(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src:
            src.state.pop("host_key", None)
            src.state.pop("transfer_host_key", None)
            self.store.set_source_state(sid, src.state)
            await event.answer("Pinned key forgotten — the next connection pins the new one.", alert=True)
            await self.show_source(event, sid)

    async def run_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src:
            await self.start_run(event, [sid], src.name)

    async def test_source(self, event, sid: int) -> None:
        src = self.store.get_source(sid)
        if src is None:
            return
        if isinstance(event, events.CallbackQuery.Event):
            await event.answer("Testing…")
        msg = await self.app.client.send_message(event.chat_id, f"🧪 Testing <b>{esc(src.name)}</b>…",
                                                 parse_mode="html")
        workdir = self.app.cfg.staging_dir / f"test-{sid}"
        try:
            cls = get_type(src.type)
            ctx = RunContext(self.store, src, workdir, jalali.now_jalali(self.app.cfg.timezone))
            result = await asyncio.wait_for(cls(src, ctx).test(), timeout=180)
            text = f"✅ <b>{esc(src.name)}</b>\n{esc(result)}"
            if ctx.warnings:
                text += "\n" + "\n".join(f"⚠️ {esc(w)}" for w in ctx.warnings)
        except asyncio.TimeoutError:
            text = f"❌ <b>{esc(src.name)}</b>: test timed out after 3 minutes"
        except SourceError as e:
            text = f"❌ <b>{esc(src.name)}</b>: {esc(e)}"
        except Exception as e:  # noqa: BLE001
            log.info("test of %s failed", src.name, exc_info=True)
            text = f"❌ <b>{esc(src.name)}</b>: {esc(short_error(e))}"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        await msg.edit(text[:4000], parse_mode="html",
                       buttons=[[Button.inline("⬅️ Back to source", f"sv:{sid}".encode())]])

    # ======================================================================
    # runs / history
    # ======================================================================
    async def start_run(self, event, source_ids: list[int] | None, label: str) -> None:
        engine = self.app.engine
        if source_ids is None and not self.store.list_sources(enabled_only=True):
            raise SourceError("There are no enabled sources yet.")
        if source_ids is not None and not source_ids:
            raise SourceError("Nothing to run.")
        sender = await event.get_sender()
        who = getattr(sender, "first_name", None) or str(event.sender_id)
        note = " (its uploads wait for the run in progress)" if engine.busy else ""
        msg = await self.app.client.send_message(event.chat_id, f"⏳ Starting backup of {esc(label)}…{note}",
                                                 parse_mode="html")

        async def on_progress(text: str) -> None:
            try:
                await msg.edit(text, parse_mode="html")
            except errors.MessageNotModifiedError:
                pass

        async def job() -> None:
            try:
                await engine.run(source_ids, trigger=f"manual · {who}", on_progress=on_progress)
            except Exception as e:  # noqa: BLE001
                log.exception("manual run failed")
                await on_progress(f"💥 Run failed: {esc(short_error(e))}")

        asyncio.create_task(job())

    async def show_history(self, event) -> None:
        runs = self.store.list_runs(15)
        rows = [[Button.inline(f"{STATUS_ICON.get(r['status'], '•')} #{r['id']} {r['jdate']} · "
                               f"{human_size(r['total_bytes'])} · {r['trigger'][:20]}", f"hv:{r['id']}".encode())]
                for r in runs]
        rows.append(BACK_MAIN)
        await self._respond(event, "📊 <b>Recent runs</b>" + ("" if runs else "\n\nNo runs yet."), rows)

    async def show_run(self, event, run_id: int) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        rows = []
        for it in self.store.run_items(run_id):
            files = json.loads(it["files"] or "[]")
            if any(f.get("send_telegram", True) for f in files):  # archive-only items are never re-sent
                rows.append([Button.inline(f"📤 Resend {it['source_name']}"[:60], f"hr:{it['id']}".encode())])
        rows.append([Button.inline("⬅️ History", b"hl"), *BACK_MAIN])
        await self._respond(event, (run["summary"] or f"Run #{run_id}: {run['status']}")[:4000], rows)

    async def resend(self, event, item_id: int) -> None:
        await event.answer("Re-sending from the archive…")
        msg = await self.app.client.send_message(event.chat_id, "📤 Re-sending…")
        try:
            result = await self.app.engine.resend_item(item_id)
        except Exception as e:  # noqa: BLE001
            result = f"❌ {short_error(e)}"
        await msg.edit(esc(result), parse_mode="html")

    # ======================================================================
    # credentials
    # ======================================================================
    def _cred_usage(self, name: str) -> list[str]:
        keys = credential_keys_by_type()
        used = [s.name for s in self.store.list_sources()
                if any(s.config.get(k) == name for k in keys.get(s.type, set()))]
        if self.store.get_setting("archive_credential") == name:
            used.append("⚙️ archive")
        return used

    async def show_credentials(self, event) -> None:
        creds = self.store.list_credentials()
        rows = [[Button.inline(f"🔑 {c.name} ({c.username})"[:60], f"cv:{c.id}".encode())] for c in creds]
        rows.append([Button.inline("➕ Add credential", b"ca"), *BACK_MAIN])
        await self._respond(event, "🔑 <b>Credentials</b> — stored encrypted.", rows)

    async def show_credential(self, event, cid: int) -> None:
        c = self.store.get_credential(cid)
        if c is None:
            await self.show_credentials(event)
            return
        used = self._cred_usage(c.name)
        text = (f"🔑 <b>{esc(c.name)}</b>\nUsername: <code>{esc(c.username)}</code>\nPassword: ••••••\n"
                + (f"Note: {esc(c.note)}\n" if c.note else "")
                + f"\nUsed by: {esc(', '.join(used)) if used else 'nothing'}")
        rows = [[Button.inline(f"✏️ {f.label}", f"cef:{cid}:{f.key}".encode()) for f in CRED_FIELDS[:2]],
                [Button.inline(f"✏️ {f.label}", f"cef:{cid}:{f.key}".encode()) for f in CRED_FIELDS[2:]],
                [Button.inline("🗑 Delete", f"cd:{cid}".encode()), Button.inline("⬅️ Credentials", b"cl")]]
        await self._respond(event, text, rows)

    async def edit_credential_field(self, event, arg: str) -> None:
        cid_s, key = arg.split(":")
        c = self.store.get_credential(int(cid_s))
        if c is None:
            return
        f = next(f for f in CRED_FIELDS if f.key == key)
        current = {"name": c.name, "username": c.username, "note": c.note}
        await self.start_form(event, FormState("cred_edit", f"🔑 {c.name}", [f], current=current, editing=True,
                                               target=c.id))

    async def confirm_delete_credential(self, event, cid: int) -> None:
        c = self.store.get_credential(cid)
        if c is None:
            return
        used = self._cred_usage(c.name)
        if used:
            raise SourceError(f"Still used by: {', '.join(used)}")
        await self._respond(event, f"🗑 Delete credential <b>{esc(c.name)}</b>?",
                            [[Button.inline("Yes, delete", f"cdy:{cid}".encode()),
                              Button.inline("No", f"cv:{cid}".encode())]])

    async def delete_credential(self, event, cid: int) -> None:
        self.store.delete_credential(cid)
        await self.show_credentials(event)

    # ======================================================================
    # schedule & settings
    # ======================================================================
    async def show_schedule(self, event) -> None:
        tz = self.app.cfg.timezone
        lines = [f"⏰ <b>Schedule</b> ({esc(tz.key)})", ""]
        groups = self.store.list_groups()
        for g in groups:
            n = len(self.store.group_members(g.name, enabled_only=True))
            lines.append(f"{'🗂' if g.enabled else '⏸'} <b>{esc(g.name)}</b>: {esc(describe_schedule(g.schedule))}"
                         f" — {n} source(s)")
        names = {g.name.lower() for g in groups}
        own, manual = [], []
        for src in self.store.list_sources(enabled_only=True):
            if (src.config.get("group") or "").lower() in names:
                continue
            (own if src.config.get("schedule") else manual).append(src)
        if own:
            lines += ["", "Sources with their own time:"]
            lines += [f"• {esc(x.name)}: {esc(describe_schedule(x.config['schedule']))}" for x in own]
        if manual:
            lines += ["", "Manual only (no group, no time): " + esc(", ".join(x.name for x in manual))]
        nxt = self.app.scheduler.next_runs()
        if nxt:
            lines += ["", "Upcoming:"]
            for when, label, n in nxt[:6]:
                j = jalali.to_jalali(when.astimezone(tz))
                lines.append(f"• {jalali.pretty(j)} — {esc(label)}, {n} source(s)")
        lines += ["", "<i>Each run uploads its files smallest first, one at a time; runs that overlap "
                  "wait for each other's uploads.</i>"]
        await self._respond(event, "\n".join(lines), [[Button.inline("🗂 Groups", b"gl")], BACK_MAIN])

    # ======================================================================
    # groups
    # ======================================================================
    async def show_groups(self, event) -> None:
        rows = []
        for g in self.store.list_groups():
            n = len(self.store.group_members(g.name))
            label = f"{'🗂' if g.enabled else '⏸'} {g.name} · {describe_schedule(g.schedule)} · {n} sources"
            rows.append([Button.inline(label[:60], f"gv:{g.id}".encode())])
        rows.append([Button.inline("➕ New group", b"ga"), *BACK_MAIN])
        text = ("🗂 <b>Backup groups</b>\n\nA group starts at its time, backs up all its sources, then uploads "
                "their files smallest first, one after another.")
        await self._respond(event, text, rows)

    async def show_group(self, event, gid: int) -> None:
        g = self.store.get_group(gid)
        if g is None:
            await self.show_groups(event)
            return
        members = self.store.group_members(g.name)
        lines = [f"🗂 <b>{esc(g.name)}</b> — {'✅ enabled' if g.enabled else '⏸ disabled'}",
                 f"⏰ {esc(describe_schedule(g.schedule))}", ""]
        if members:
            lines.append(f"Sources ({len(members)}):")
            lines += [f"• {'' if m.enabled else '⏸ '}{esc(m.name)}" for m in members]
        else:
            lines.append("No sources yet — add some with 📦 Sources in group.")
        rows = [
            [Button.inline("▶️ Run group now", f"gr:{gid}".encode()),
             Button.inline("📦 Sources in group", f"gm:{gid}".encode())],
            [Button.inline("⏰ Change time", f"ge:{gid}:schedule".encode()),
             Button.inline("🏷 Rename", f"ge:{gid}:name".encode())],
            [Button.inline("⏸ Disable" if g.enabled else "▶️ Enable", f"gx:{gid}".encode()),
             Button.inline("🗑 Delete", f"gd:{gid}".encode())],
            [Button.inline("⬅️ Groups", b"gl"), *BACK_MAIN],
        ]
        await self._respond(event, "\n".join(lines), rows)

    async def show_group_members(self, event, gid: int) -> None:
        g = self.store.get_group(gid)
        if g is None:
            return
        rows, row = [], []
        for src in self.store.list_sources():
            current = src.config.get("group") or ""
            if current.lower() == g.name.lower():
                label = f"✅ {src.name}"
            elif current:
                label = f"↪️ {src.name} ({current})"
            else:
                label = f"▫️ {src.name}"
            row.append(Button.inline(label[:40], f"gmt:{gid}:{src.id}".encode()))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button.inline("✅ Done", f"gv:{gid}".encode())])
        text = (f"📦 <b>Sources in {esc(g.name)}</b>\n\nTap to add or remove.\n✅ in this group · "
                "↪️ in another group (tap to move it here) · ▫️ no group")
        await self._respond(event, text, rows)

    async def toggle_group_member(self, event, arg: str) -> None:
        gid_s, sid_s = arg.split(":")
        g = self.store.get_group(int(gid_s))
        src = self.store.get_source(int(sid_s))
        if g is None or src is None:
            return
        inside = (src.config.get("group") or "").lower() == g.name.lower()
        self.store.set_source_group(src.id, "" if inside else g.name)
        self.app.scheduler.reload()
        await self.show_group_members(event, g.id)

    async def edit_group_field(self, event, arg: str) -> None:
        gid_s, key = arg.split(":")
        g = self.store.get_group(int(gid_s))
        if g is None:
            return
        f = next(f for f in GROUP_FIELDS if f.key == key)
        await self.start_form(event, FormState("group_edit", f"🗂 {g.name}", [f], editing=True, target=g.id,
                                               current={"name": g.name, "schedule": g.schedule}))

    async def run_group(self, event, gid: int) -> None:
        g = self.store.get_group(gid)
        if g:
            ids = [m.id for m in self.store.group_members(g.name, enabled_only=True)]
            await self.start_run(event, ids, f"group {g.name}")

    async def toggle_group(self, event, gid: int) -> None:
        g = self.store.get_group(gid)
        if g:
            self.store.save_group(g.name, g.schedule, enabled=not g.enabled, group_id=g.id)
            self.app.scheduler.reload()
            await self.show_group(event, gid)

    async def confirm_delete_group(self, event, gid: int) -> None:
        g = self.store.get_group(gid)
        if g:
            await self._respond(event, f"🗑 Delete group <b>{esc(g.name)}</b>? Its sources stay, without a group "
                                       "(they then run only at their own time, or manually).",
                                [[Button.inline("Yes, delete", f"gdy:{gid}".encode()),
                                  Button.inline("No", f"gv:{gid}".encode())]])

    async def delete_group(self, event, gid: int) -> None:
        self.store.delete_group(gid)
        self.app.scheduler.reload()
        await self.show_groups(event)

    async def show_settings(self, event) -> None:
        lines = ["⚙️ <b>Settings</b>", ""]
        rows, row = [], []
        for f in SETTINGS_FIELDS:
            val = self.store.get_setting(f.key)
            lines.append(f"• {esc(f.label)}: <code>{esc(f.display(val))}</code>")
            if f.key == "chat_id":
                continue  # set with /bind
            row.append(Button.inline(f.label[:30], f"setk:{f.key}".encode()))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button.inline("🧪 Test archive", b"seta"), Button.inline("♻️ Reset topics", b"sett")])
        rows.append(BACK_MAIN)
        await self._respond(event, "\n".join(lines), rows)

    async def edit_setting(self, event, key: str) -> None:
        f = SETTINGS_BY_KEY[key]
        await self.start_form(event, FormState("setting", "⚙️ Settings", [f], current={key: self.store.get_setting(key)}
                                               if not f.secret else {}, editing=not f.secret, target=key))

    async def test_archive(self, event) -> None:
        await event.answer("Testing archive…")
        try:
            text = "✅ " + await asyncio.wait_for(self.app.engine.archive.test(), timeout=60)
        except Exception as e:  # noqa: BLE001
            text = f"❌ Archive: {short_error(e)}"
        await self.app.client.send_message(event.chat_id, esc(text), parse_mode="html")

    async def reset_topics(self, event) -> None:
        self.store.set_json(TOPICS_KEY, {})
        await event.answer("Topic cache cleared — topics are recreated on the next post.", alert=True)

    async def bind_group(self, event) -> None:
        chat = await event.get_chat()
        self.store.set_setting("chat_id", str(event.chat_id))
        topics = self.store.get_json(TOPICS_KEY, {}) or {}
        topics.pop(str(event.chat_id), None)
        self.store.set_json(TOPICS_KEY, topics)
        lines = [f"✅ Backups will be posted here (chat ID <code>{event.chat_id}</code>)."]
        is_forum = bool(getattr(chat, "forum", False))
        try:
            perms = await self.app.client.get_permissions(chat, "me")
            if not perms.is_admin:
                lines.append("⚠️ I'm not an admin here — make me admin so I can post files and create topics.")
        except Exception:  # noqa: BLE001
            pass
        if is_forum and self.store.get_setting("topic_mode") != "none":
            made = []
            for cat in ("databases", "network", "websites", "files", "reports"):
                if await self.app.sender.topic_id(CATEGORY_TITLES[cat]):
                    made.append(CATEGORY_TITLES[cat])
            lines.append("Topics ready: " + (", ".join(made) if made else
                                             "⚠️ couldn't create topics — give me the 'Manage topics' right"))
        elif not is_forum:
            lines.append("ℹ️ Topics are off in this group, so everything goes to the main chat. "
                         "Enable Topics in the group settings and send /bind again to use topics.")
        await event.reply("\n".join(lines), parse_mode="html")

    # ======================================================================
    # admins
    # ======================================================================
    async def show_admins(self, event) -> None:
        owners = self.app.cfg.owner_ids
        lines = ["👥 <b>Admins</b>", ""]
        lines += [f"• <code>{o}</code> 🔒 owner (.env)" for o in sorted(owners)]
        rows = []
        for a in self.store.list_admins():
            lines.append(f"• <code>{a['user_id']}</code> {esc(a['name'])}")
            if event.sender_id in owners:
                rows.append([Button.inline(f"➖ Remove {a['name'] or a['user_id']}"[:60], f"ad:{a['user_id']}".encode())])
        if event.sender_id in owners:
            rows.append([Button.inline("➕ Add admin", b"aa")])
        else:
            lines.append("\n<i>Only owners can add or remove admins.</i>")
        rows.append(BACK_MAIN)
        await self._respond(event, "\n".join(lines), rows)

    async def add_admin(self, event) -> None:
        if event.sender_id not in self.app.cfg.owner_ids:
            raise SourceError("Only owners can add admins")
        await self.start_form(event, FormState("admin_add", "👥 Add admin", ADMIN_FIELDS))

    async def remove_admin(self, event, uid: int) -> None:
        if event.sender_id not in self.app.cfg.owner_ids:
            raise SourceError("Only owners can remove admins")
        self.store.remove_admin(uid)
        await self.show_admins(event)

    # ======================================================================
    # forms
    # ======================================================================
    def _cred_names(self) -> list[str]:
        return [c.name for c in self.store.list_credentials()]

    def _group_names(self) -> list[str]:
        return [g.name for g in self.store.list_groups()]

    async def start_form(self, event, state: FormState) -> None:
        self.forms[event.sender_id] = state
        await self._ask(event, state)

    async def _ask(self, event, state: FormState) -> None:
        if state.current_field() is None:
            await self._finish(event, state)
            return
        text, buttons = prompt(state, self._cred_names(), self._group_names())
        if isinstance(event, events.CallbackQuery.Event):
            await self._respond(event, text, buttons)
            state.message_id = event.message_id
        else:
            msg = await event.respond(text, buttons=buttons, parse_mode="html", link_preview=False)
            state.message_id = msg.id

    async def _close_prompt(self, event, state: FormState, shown: str) -> None:
        """Turn the answered prompt into a one-line receipt (keeps the chat readable)."""
        if state.message_id:
            try:
                await self.app.client.edit_message(event.chat_id, state.message_id, shown, parse_mode="html",
                                                   buttons=None)
            except errors.RPCError:
                pass

    def _validate_unique(self, state: FormState, f: Field, value: Any) -> None:
        if f.key != "name":
            return
        if state.kind in ("source_add", "source_rename"):
            other = self.store.get_source_by_name(value)
            if other and other.id != state.target:
                raise FieldError(f"A source named '{value}' already exists")
        if state.kind in ("cred_add", "cred_edit"):
            other = self.store.get_credential_by_name(value)
            if other and other.id != state.target:
                raise FieldError(f"A credential named '{value}' already exists")
        if state.kind in ("group_add", "group_edit"):
            other = self.store.get_group_by_name(value)
            if other and other.id != state.target:
                raise FieldError(f"A group named '{value}' already exists")

    async def form_text(self, event, state: FormState, text: str) -> None:
        f = state.current_field()
        if f is None:
            return
        if f.kind == SECRET:
            try:
                await event.delete()
            except errors.RPCError:
                pass
        try:
            value = f.parse(text)
            self._validate_unique(state, f, value)
            if f.kind == CREDENTIAL and value and not self.store.get_credential_by_name(value):
                raise FieldError(f"No credential named '{value}' — pick one from the buttons")
            if f.kind == GROUP and value:
                g = self.store.get_group_by_name(value)
                if g is None:
                    raise FieldError(f"No group named '{value}' — pick one from the buttons")
                value = g.name
        except FieldError as e:
            await event.respond(f"⚠️ {esc(e)}\nTry again, or /cancel.", parse_mode="html")
            return
        state.values[f.key] = value
        state.index += 1
        await self._close_prompt(event, state, f"✔️ {esc(f.label)}: <code>{esc(f.display(value))}</code>")
        state.message_id = None
        await self._ask(event, state)

    async def form_button(self, event, arg: str) -> None:
        uid = event.sender_id
        state = self.forms.get(uid)
        if state is None:
            await event.answer("This form has expired.", alert=True)
            return
        action, _, rest = arg.partition(":")
        f = state.current_field()

        if action == "cancel":
            self.forms.pop(uid, None)
            if state.parent is not None:
                self.forms[uid] = state.parent
                await self._ask(event, state.parent)
            else:
                await self._respond(event, "Cancelled.", [BACK_MAIN])
            return
        if action in ("save", "savetest"):
            await self._save_new_source(event, state, test=action == "savetest")
            return
        if f is None:
            return
        if action == "yes":
            state.accept(True)
        elif action == "no":
            state.accept(False)
        elif action == "v":
            state.accept(f.choices[int(rest)])
        elif action == "c":
            state.accept(state.choices[int(rest)])
        elif action == "def":
            state.use_default()
        elif action == "keep":
            state.keep_current()
        elif action == "clr":
            state.clear()
        elif action == "newcred":
            child = FormState("cred_add", "➕ New credential", CRED_FIELDS, parent=state, parent_field=f.key)
            self.forms[uid] = child
            await self._ask(event, child)
            return
        await self._ask(event, state)

    async def _finish(self, event, state: FormState) -> None:
        uid = event.sender_id
        kind, vals = state.kind, state.values

        if kind == "source_add":
            buttons = [[Button.inline("💾 Save", b"f:save"), Button.inline("💾 Save & test", b"f:savetest")],
                       [Button.inline("✖️ Cancel", b"f:cancel")]]
            await self._respond(event, summary(state) + "\n\nSave this source?", buttons)
            return

        self.forms.pop(uid, None)
        if kind == "source_edit":
            src = self.store.get_source(state.target)
            cls = get_type(src.type)
            self.store.update_source(src.id, config={**src.config, **vals}, secret_keys=cls.secret_keys())
            if "schedule" in vals or "group" in vals:
                self.app.scheduler.reload()
            await self.show_source_edit(event, src.id)
        elif kind == "source_rename":
            self.store.update_source(state.target, name=vals["name"])
            await self.show_source(event, state.target)
        elif kind in ("cred_add", "cred_edit"):
            if kind == "cred_add":
                cid = self.store.save_credential(vals["name"], vals["username"], vals["password"], vals.get("note", ""))
            else:
                c = self.store.get_credential(state.target)
                new_name = vals.get("name", c.name)
                self.store.save_credential(new_name, vals.get("username", c.username), vals.get("password"),
                                           vals.get("note", c.note), cred_id=c.id)
                if new_name != c.name:
                    self.store.rename_credential_refs(c.name, new_name, credential_keys_by_type())
                cid = c.id
            if state.parent is not None:
                parent = state.parent
                parent.values[state.parent_field] = vals["name"]
                parent.index += 1
                self.forms[uid] = parent
                await self._ask(event, parent)
                return
            await self.show_credential(event, cid)
        elif kind == "setting":
            key = state.target
            self.store.set_setting(key, vals[key])
            if key in ("chat_id", "topic_mode"):
                self.store.set_json(TOPICS_KEY, {})
            await self.show_settings(event)
        elif kind == "group_add":
            gid = self.store.save_group(vals["name"], vals["schedule"])
            self.app.scheduler.reload()
            await self.show_group_members(event, gid)
        elif kind == "group_edit":
            g = self.store.get_group(state.target)
            self.store.save_group(vals.get("name", g.name), vals.get("schedule", g.schedule), g.enabled, group_id=g.id)
            self.app.scheduler.reload()
            await self.show_group(event, g.id)
        elif kind == "admin_add":
            self.store.add_admin(vals["user_id"], vals.get("name", ""), uid)
            await self.show_admins(event)

    async def _save_new_source(self, event, state: FormState, test: bool) -> None:
        cls = get_type(state.type_id)
        vals = dict(state.values)
        name = vals.pop("name")
        if self.store.get_source_by_name(name):
            raise FieldError(f"A source named '{name}' already exists")
        for f in cls.all_fields():  # fill defaults for fields the wizard skipped (advanced/hidden)
            vals.setdefault(f.key, f.default)
        sid = self.store.create_source(name, cls.type_id, vals, cls.secret_keys())
        self.forms.pop(event.sender_id, None)
        self.app.scheduler.reload()
        await self.show_source(event, sid)
        if test:
            await self.test_source(event, sid)
