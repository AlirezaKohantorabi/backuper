# tests/test_panel.py

Drives the real `Panel` with fake Telegram events — a complete admin session without Telegram. It caught
real bugs (a `//server/share` answer treated as a command; the add wizard not asking the group questions).

| Name | Role |
|---|---|
| `OWNER` | The admin's user id in the test. |
| `FakeMsg` / `FakeMsg.__init__` / `FakeMsg.edit` | A sent message that can be edited. |
| `FakeClient` / `FakeClient.__init__` / `FakeClient.send_message` / `FakeClient.edit_message` / `FakeClient.send_file` / `FakeClient.add_event_handler` | Minimal Telethon client (records sent files). |
| `FakeCB` / `FakeCB.__init__` / `FakeCB.edit` / `FakeCB.answer` / `FakeCB.get_sender` | A button press (records alerts and the edited text/buttons). |
| `FakeText` / `FakeText.__init__` / `FakeText.respond` / `FakeText.delete` | A typed message (records replies and whether it was deleted). |
| `panel` | Fixture: a `Panel` with a real store, engine (no Telegram) and scheduler. |
| `_data` / `all_buttons` | Read callback data from buttons (Telethon ≥ 1.45 keeps it in `button.type.data`). |
| `press` / `say` | Press a button / send text; `press` fails the test on unexpected errors. |
| `test_full_admin_flow` | Non-admin refused; credential form (password message deleted); add a SQL source with validation errors; edit, toggle, duplicate, rename; nested "new credential"; groups (create, upload-from, members, rename follows, schedule page); moving a source between groups from its page; one-time run (bad input refused, created, listed, cancelled); 📄 Log (clear message when missing, file sent when present); settings; admins (owner only); deleting a used credential refused; history; delete source. |
