# Tests

Run them with `.venv/bin/pytest` (≈ 7 s, no network needed). They use fakes instead of real servers
and Telegram, so they run anywhere; real-world behaviour was additionally verified against the first
site's servers (see [../../LESSONS.md](../../LESSONS.md)).

| File | What it proves |
|---|---|
| [conftest.py](conftest.md) | shared fixtures (temporary config + database) |
| [test_db.py](test_db.md) | secrets are encrypted at rest; settings defaults; credential renames follow |
| [test_docs.py](test_docs.md) | every code file has a document and every function/class is mentioned in it |
| [test_engine.py](test_engine.md) | whole runs: ordering, archive, retention, failures, groups' upload queue, copy mode… |
| [test_fields.py](test_fields.md) | schedule/path/value parsing |
| [test_forms_scheduler.py](test_forms_scheduler.md) | form navigation; scheduling of groups, own times, one-time runs; legacy migration |
| [test_hosting.py](test_hosting.md) | download/delete vs keep-newest-on-host for hosting panels |
| [test_jalali.py](test_jalali.md) | one-time run time parsing; date folders |
| [test_logging.py](test_logging.md) | run logs: tagged lines, only that run, steps present, handler removed, pruning |
| [test_mssql.py](test_mssql.md) | database-name inference, grouping, labels (incl. Holoo's real file names) |
| [test_status.py](test_status.md) | live status file: progress text, running/idle content, written locally and to the archive |
| [test_smb_guard.py](test_smb_guard.md) | SMB: own connection per client, waiting for credits, no silent short copies |
| [test_self_backup.py](test_self_backup.md) | the bot's own backup: database snapshot, .env, local/ |
| [test_control.py](test_control.md) | restart requests: now, when idle, back-online notice |
| [test_packager.py](test_packager.md) | AES zips, locked files, splitting, storing incompressible data |
| [test_panel.py](test_panel.md) | a complete simulated admin session in the Telegram panel |
