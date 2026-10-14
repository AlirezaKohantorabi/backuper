# backuper/bot/panel.py

**Purpose:** the whole Telegram admin panel — commands, menus, buttons and the completion logic of
every form. It only talks to the rest of the program through the `BotApp` it gets
(`app.store`, `app.engine`, `app.scheduler`, `app.sender`, `app.client`, `app.cfg`).

**Uses:** [forms.py](forms.md), [fields.py](../fields.md), [settings.py](../settings.md),
[sources registry](../sources/__init__.md), [jalali.py](../jalali.md), [db.py](../db.md),
[engine.py](../engine.md), [scheduler.py](../scheduler.md), [destinations/telegram.py](../destinations/telegram.md).

## How it works

- `on_message` handles commands and typed form answers; `on_callback` handles button presses.
- Every button carries short callback data (Telegram limits it to 64 bytes) such as `sv:12`
  (source view 12) or `gmt:3:12` (toggle source 12 in group 3). `_routes()` maps the prefix to a method.
- Only admins get past the first line of both handlers. Non-admins are told their Telegram ID on `/start`.
- Commands only count when they are known commands — a typed path like `//server/share` is form input,
  not a command (a real bug found by the simulated-panel test).
- Password answers are deleted from the chat right after they're read.

## Menu map

```
🏠 Main ── 📦 Sources ── source page ── ▶️ Run now · 🧪 Test · 🗂 Group · ⏱ Run once · ✏️ Edit · ⏸ · 📄 Duplicate · 🗑
        │             └─ ➕ Add source → type → wizard → 💾 Save / Save & test
        ├─ 🔑 Credentials ── page ── ✏️ fields · 🗑 (refused while in use)
        ├─ ▶️ Run all now
        ├─ 📊 History ── run ── 📤 Resend (zips that went to Telegram) · 📄 Log
        ├─ 🗂 Groups ── group page ── sources (buttons) · ▶️ Run group · ⏱ Run once · 📦 Add/remove
        │                            · ⏰ Start time · 📤 Upload from · 🏷 Rename · ⏸ · 🗑
        ├─ ⏰ Schedule (groups, own times, manual-only, upcoming, one-time runs with ✖️ cancel)
        ├─ ⚙️ Settings (+ 🧪 Test archive, ♻️ Reset topics)
        ├─ 👥 Admins (owners only can add/remove)
        └─ ❓ Help
Commands: /start /menu /run [Name] /status /cancel /id /help, and /bind inside the group.
```

## Module-level names

| Name | What it is |
|---|---|
| `log` | Logger. |
| `CAT_ICON` | Emoji per source category. |
| `PAGE` | Sources per page in the list. |
| `BACK_MAIN` | The "🏠 Main menu" button row. |
| `NAME_FIELD` | The "Name" question that starts every add-source wizard. |
| `CRED_FIELDS` | Credential form: name, username, password/token (secret), note. |
| `GROUP_FIELDS` | Group form: name, start time, upload from. |
| `ONCE_FIELD` | "When" question for one-time runs. |
| `ADMIN_FIELDS` | Add-admin form: Telegram user id, name. |
| `HELP` | The ❓ Help text. |

## Panel methods

| Method | What it does |
|---|---|
| `Panel.__init__` | Keeps the app, store and the per-user open forms. |
| `Panel.register` | Registers the message and button handlers with Telethon. |
| `Panel._cmd` | Splits a message into (command, argument) — only for known commands. |
| `Panel._respond` | Edit the message behind a button, or send a new message for typed input. |
| `Panel.on_message` | Commands, `/id`, `/bind` in the group, typed form answers. |
| `Panel.on_callback` | Button presses: admin check, route, show `FieldError`/`SourceError` as an alert, log anything else. |
| `Panel._routes` | Callback prefix → method. |
| `Panel.show_main` | Main menu with counts, next run, last run, warnings (no group bound, no zip password, no archive). |
| `Panel.show_status` | Live progress of running runs, or the last report. |
| `Panel.show_sources` | Paged list of sources (⏸ marks disabled ones). |
| `Panel._source_text` | A source's page text: type, when it runs, every field value (secrets masked), pinned key, last result. |
| `Panel.show_source` | A source's page ("profile") with its buttons. |
| `Panel.choose_type` | "Add source" — list of source types with descriptions. |
| `Panel.start_add_source` | Starts the wizard: name + the type's fields + group/own time/Telegram (advanced fields are skipped). |
| `Panel.show_source_edit` | Field list of a source; tap one to change it. |
| `Panel.edit_source_field` | Opens a one-field form for a source field. |
| `Panel.rename_source` | Rename form (names are unique). |
| `Panel.toggle_source` | Enable/disable (reloads the schedule). |
| `Panel.confirm_delete_source` / `Panel.delete_source` | Delete with confirmation (archived zips stay). |
| `Panel.duplicate_source` | Copy a source (disabled) — fastest way to add similar servers. |
| `Panel.forget_keys` | Forget a pinned SSH host key (after a router/host was replaced). |
| `Panel._when_text` | "When does this run?" line: group (start/upload time), own time, or manual; plus pending one-time runs. |
| `Panel.choose_source_group` / `Panel.set_source_group` | Move a source into a group or out of groups, from its own page. |
| `Panel.schedule_once_source` / `Panel.schedule_once_group` | Start the "⏱ Run once at…" form for a source / a group. |
| `Panel.cancel_once` | Cancel a pending one-time run. |
| `Panel._create_once` | Stores the one-time run (source ids are fixed at creation) and reloads the schedule. |
| `Panel.run_source` | ▶️ Run now for one source. |
| `Panel.test_source` | 🧪 Test: runs the plugin's `test()` (3-minute limit) and shows the result. |
| `Panel.start_run` | Starts a manual run in the background and keeps a progress message updated; manual runs upload as soon as they're done. |
| `Panel.show_history` / `Panel.show_run` | Recent runs / one run's report with 📤 Resend buttons (only for items that went to Telegram). |
| `Panel.resend` | Re-uploads an item's zips from the archive. |
| `Panel.send_run_log` | 📄 Log: sends the run's detailed log file (every step, timing and error of that run). |
| `Panel._cred_usage` | Which sources/settings use a credential (deleting is refused while used). |
| `Panel.show_credentials` / `Panel.show_credential` | Credential list / page (password never shown). |
| `Panel.edit_credential_field` | Change name, username, password or note. Renames update every reference. |
| `Panel.confirm_delete_credential` / `Panel.delete_credential` | Delete (only when unused). |
| `Panel.show_schedule` | ⏰ Schedule overview: groups, own times, manual-only sources, upcoming runs, one-time runs with cancel buttons. |
| `Panel.show_groups` / `Panel.show_group` | Group list / a group's page (members as buttons to their pages). |
| `Panel.show_group_members` / `Panel.toggle_group_member` | Add/remove/move sources: ✅ in this group, ↪️ in another (tap to move here), ▫️ no group. |
| `Panel.edit_group_field` | Change start time, upload-from time or name. |
| `Panel.run_group` | ▶️ Run group now (uploads right away). |
| `Panel.toggle_group` / `Panel.confirm_delete_group` / `Panel.delete_group` | Enable/disable/delete a group (members become ungrouped). |
| `Panel.show_settings` / `Panel.edit_setting` | ⚙️ Settings page and editing (the group binding is done with `/bind`). |
| `Panel.test_archive` | Write test + free space of the archive. |
| `Panel.reset_topics` | Forget cached Telegram topic ids (they're recreated on the next post). |
| `Panel.bind_group` | `/bind` in a group: makes it the target, checks admin rights, creates the topics. |
| `Panel.show_admins` / `Panel.add_admin` / `Panel.remove_admin` | Admin list; only owners from `.env` may add/remove. |
| `Panel._cred_names` / `Panel._group_names` | Names offered as buttons in forms. |
| `Panel.start_form` / `Panel._ask` | Open a form and ask its current question. |
| `Panel._close_prompt` | Turns an answered question into a one-line receipt (keeps the chat readable). |
| `Panel._validate_unique` | Unique names for sources, credentials and groups. |
| `Panel.confirm_restart` | 🔄 Restart (main menu or `/restart`): shows running backups; *When backups finish* / *Now — stop the backups* / *Restart now* / Cancel. |
| `Panel.restart` | Writes the restart request ([control](../control.md)) with who asked and the chat to notify. |
| `Panel.form_text` | A typed answer: parse/validate (incl. one-time run times and credential/group names), delete secret messages, move on. |
| `Panel.form_button` | A button answer inside a form (yes/no, choice, credential/group, default, keep, empty, new credential, cancel, save). |
| `Panel._finish` | What a completed form does, per kind (save source, credential, group, setting, admin, one-time run…). |
| `Panel._save_new_source` | Creates the source (defaults for skipped fields), reloads the schedule, optionally tests it. |
