# Code documentation

One document per code file, mirroring the repository layout
(`backuper/engine.py` → [`backuper/engine.md`](backuper/engine.md)). Every
function, class and method in a file is listed in its document — a test
(`tests/test_docs.py`) fails if one is missing, so these pages can't silently
go out of date.

Start with [../PROJECT.md](../PROJECT.md) for the big picture, then read the
files in the order below.

## Reading order

1. [models.md](backuper/models.md) — the data shapes everything passes around
2. [fields.md](backuper/fields.md) — declarative form fields (drive the bot's forms and the YAML import)
3. [db.md](backuper/db.md) — the SQLite store (sources, credentials, groups, runs…)
4. [sources/base.md](backuper/sources/base.md) — what a backup "source" plugin is
5. [engine.md](backuper/engine.md) — what a run does, phase by phase
6. [scheduler.md](backuper/scheduler.md) — when runs happen
7. [bot/panel.md](backuper/bot/panel.md) — the Telegram admin panel
8. the rest as needed

## How the pieces connect

```
                 ┌──────────────┐  serve   ┌──────────────┐    ┌──────────────┐
  systemd ──────►│   cli.py     │─────────►│  bot/app.py  │───►│ bot/panel.py │──► bot/forms.py
                 └──────┬───────┘          └──────┬───────┘    └──────┬───────┘
                        │ (archive-only mode)     │                   │ ▶️ Run now / ⏱ once
                        ▼                         ▼                   ▼
                 ┌──────────────┐  fires   ┌──────────────────────────────────┐
                 │ scheduler.py │─────────►│ engine.py  (collect → zip/copy → │
                 └──────────────┘          │   archive → upload → retention)  │
                                           └──┬───────────┬──────────┬───────┘
                       sources/*  ◄───────────┘           │          │
            mssql_collect  mssql_trigger  mikrotik        ▼          ▼
            smb_folder  remote_folder  cpanel  directadmin  packager.py   destinations/
                  │            │           │                 (AES zip)   archive.py  telegram.py
                  ▼            ▼           ▼                                  │           │
            transport/smb  transport/ssh  transport/remote ◄───────────────────┘      Telethon
                                (FTP/FTPS/SFTP)                                     (MTProto)

  db.py (SQLite)  ◄── used by everything above; secrets encrypted by crypto.py
  settings.py / fields.py / models.py / util.py / jalali.py / config.py — shared helpers
```

## All files

| File | Purpose |
|---|---|
| [backuper/\_\_init\_\_.py](backuper/__init__.md) | package version |
| [backuper/\_\_main\_\_.py](backuper/__main__.md) | `python -m backuper` entry point |
| [backuper/cli.py](backuper/cli.md) | command line: `serve`, `run`, `test`, `import`, `export`, `credential`, `set`… |
| [backuper/config.py](backuper/config.md) | bootstrap settings from `.env` |
| [backuper/crypto.py](backuper/crypto.md) | encrypt/decrypt stored secrets |
| [backuper/db.py](backuper/db.md) | SQLite persistence |
| [backuper/engine.py](backuper/engine.md) | a backup run, end to end |
| [backuper/fields.py](backuper/fields.md) | form field schema, parsing and display |
| [backuper/jalali.py](backuper/jalali.md) | Jalali dates, one-time run times |
| [backuper/models.py](backuper/models.md) | dataclasses shared by all modules |
| [backuper/packager.py](backuper/packager.md) | AES-256 zip creation, splitting for Telegram |
| [backuper/scheduler.py](backuper/scheduler.md) | groups / own times / one-time runs → timers |
| [backuper/settings.py](backuper/settings.md) | the global settings shown in ⚙️ Settings |
| [backuper/util.py](backuper/util.md) | small helpers (sizes, names, retries) |
| [backuper/bot/\_\_init\_\_.py](backuper/bot/__init__.md) | package marker |
| [backuper/bot/app.py](backuper/bot/app.md) | Telegram client + wiring (engine, scheduler, panel) |
| [backuper/bot/forms.py](backuper/bot/forms.md) | one-question-at-a-time form engine |
| [backuper/bot/panel.py](backuper/bot/panel.md) | every menu, button and command of the admin panel |
| [backuper/destinations/\_\_init\_\_.py](backuper/destinations/__init__.md) | package marker |
| [backuper/destinations/archive.py](backuper/destinations/archive.md) | the dated archive (SMB share or local folder) |
| [backuper/destinations/telegram.py](backuper/destinations/telegram.md) | uploads, topics, reports over MTProto |
| [backuper/sources/\_\_init\_\_.py](backuper/sources/__init__.md) | registry of source types |
| [backuper/sources/base.py](backuper/sources/base.md) | `SourceType` base class + `RunContext` |
| [backuper/sources/mssql_collect.py](backuper/sources/mssql_collect.md) | collect .bak files a SQL job made |
| [backuper/sources/mssql_trigger.py](backuper/sources/mssql_trigger.md) | run `BACKUP DATABASE`, then collect |
| [backuper/sources/mssql_common.py](backuper/sources/mssql_common.md) | grouping/freshness/retention shared by both SQL types |
| [backuper/sources/mikrotik.py](backuper/sources/mikrotik.md) | RouterOS export + binary backup over SSH/SFTP |
| [backuper/sources/smb_folder.py](backuper/sources/smb_folder.md) | Windows folder → zip or plain copy |
| [backuper/sources/remote_folder.py](backuper/sources/remote_folder.md) | folder over SFTP/FTP/FTPS |
| [backuper/sources/hosting_common.py](backuper/sources/hosting_common.md) | download/delete leg for hosting panels |
| [backuper/sources/cpanel.py](backuper/sources/cpanel.md) | cPanel full account backup |
| [backuper/sources/directadmin.py](backuper/sources/directadmin.md) | DirectAdmin user backup |
| [backuper/transport/\_\_init\_\_.py](backuper/transport/__init__.md) | package marker |
| [backuper/transport/smb.py](backuper/transport/smb.md) | SMB client wrapper (list, read, write, server-side copy) |
| [backuper/transport/ssh.py](backuper/transport/ssh.md) | SSH with host-key pinning |
| [backuper/transport/remote.py](backuper/transport/remote.md) | one interface over FTP, FTPS and SFTP |
| [tests/…](tests/README.md) | what every test file proves |
| [install.sh, deploy/backuper.service, docs/…scripts](scripts.md) | installer, systemd unit, Windows/SQL/MikroTik helper scripts |

## Conventions used everywhere

- **Async on the outside, threads for blocking I/O.** The bot and engine run on one asyncio loop.
  SMB (smbprotocol), SQL (pymssql), FTP (ftplib) and zipping are blocking, so they run in
  `asyncio.to_thread(...)`. SSH/SFTP (asyncssh), HTTP (httpx) and Telegram (Telethon) are native async.
- **`SourceError` = an expected failure** with a message written for the operator (shown in the
  report without a traceback). Any other exception is logged with a traceback and shown short.
- **Secrets never leave the encrypted store in clear text**: they are decrypted only in memory,
  never logged, never put in reports (`password=***` redaction in SSH errors), and password messages
  are deleted from the Telegram chat.
- **Schema-driven UI.** A source type lists `Field`s; the bot builds add/edit forms from them and the
  YAML importer validates with them. Adding a source type needs no bot code.
- **Nothing is deleted unless it is safely stored somewhere else first** (source retention runs only
  after the new backup reached the archive or Telegram), and archive retention only touches folders
  whose name is a Jalali date.
