# Backuper — project documentation

> Read this first. It explains what the system does, how it is built and why, and where to look for
> details. Code-level documentation (one page per file) is in [code/](code/README.md); installation in
> [SETUP.md](SETUP.md); every source type's requirements in [SOURCES.md](SOURCES.md); the reasons behind
> design choices in [DECISIONS.md](DECISIONS.md); what we learned in [LESSONS.md](LESSONS.md); versions in
> [../CHANGELOG.md](../CHANGELOG.md).

## 1. What it is

Backuper collects backups from many places on a company network and the internet, packs each one into
an encrypted zip (or a plain dated folder), keeps them in a dated archive on a file server, and sends them
to a private Telegram group. It is administered entirely from a Telegram bot, and it is built to be
reused: a new company = clone, install, new bot, add servers (in the bot or from a YAML file).

**Backed up today:** SQL Server databases (by collecting the backups a server already makes, or by making
them itself), MikroTik routers (full config export with secrets + binary backup), Windows folders (zip or
plain copy), folders over SFTP/FTP/FTPS, cPanel and DirectAdmin hosting accounts.

**Design goals** (from the first requirements): everything automatic and controllable from Telegram;
nothing sensitive in clear text in Telegram; big files must not block small ones (smallest first); backups
must not stay on hosting servers; dates in the Persian calendar; reusable in other companies.

## 2. A day in the life

```
07:00  group "Files"  ─► Edari, Finance (plain copies, archive only), iCan Upload (zip, archive only)
08:00  group "Daily"  ─► for each source, in parallel (3 at a time):
                          SQL "run BACKUP" ─► BACKUP DATABASE … COPY_ONLY, COMPRESSION ─► read .bak over SMB
                          SQL "collect"    ─► newest .bak in the share (skipped if nothing new)
                          MikroTik         ─► /export show-sensitive + /system backup save ─► SFTP download
                          cPanel/DA        ─► panel API: full backup ─► wait ─► FTPS download ─► delete on host
                       ─► AES-256 zip ─► E:\Backuper\1405-07-22\<Source>_<item>_1405-07-22_0800.zip
                       ─► (wait for "Upload from" time, if set)
                       ─► Telegram: smallest file first, one at a time, ≤ 1.95 GB per part
                       ─► delete old backups on the servers ("keep last N"), archive keeps 14 days
                       ─► report in the 📊 Reports topic; DM to admins if something failed
anytime  ▶️ Run now / ⏱ one-time run ─► same steps, uploads as soon as it's done
```

## 3. Architecture

One Python 3.12 process (`backuper serve`, run by systemd) with one asyncio event loop:

| Layer | Modules | Role |
|---|---|---|
| Entry | `cli.py`, `bot/app.py` | Starts everything; bot mode or archive-only mode. |
| Control | `bot/panel.py`, `bot/forms.py`, `scheduler.py` | Admin panel, forms, timers. |
| Core | `engine.py`, `packager.py` | A run: collect → package → store → upload → retention → report. |
| Plugins | `sources/*` | One class per kind of backup; declares fields, implements `collect()`. |
| Outputs | `destinations/archive.py`, `destinations/telegram.py` | Dated archive; Telegram uploads/topics/reports. |
| I/O | `transport/smb.py`, `transport/ssh.py`, `transport/remote.py` | SMB2/3, SSH/SFTP, FTP/FTPS. |
| Data | `db.py`, `crypto.py`, `models.py`, `fields.py`, `settings.py`, `config.py` | SQLite store, encryption, schemas. |

Blocking libraries (SMB, SQL, FTP, zip) run in worker threads; SSH, HTTP and Telegram are native async.
A diagram of how the files connect is in [code/README.md](code/README.md).

**Main libraries:** Telethon (Telegram MTProto), smbprotocol (SMB, pure Python), asyncssh, pymssql
(FreeTDS bundled — no ODBC driver), pyzipper (AES zip), APScheduler 3, httpx, jdatetime, cryptography (Fernet).

## 4. Concepts

| Concept | Meaning |
|---|---|
| **Source** | One thing to back up (a SQL server's databases, a router, a folder, a hosting account) with its settings. Has a *type* (plugin). |
| **Credential** | A named login (username + encrypted password/token), reused by any number of sources. |
| **Group** | Sources that run together at a start time; one run, uploads smallest first; optional *Upload from* time. |
| **Own time** | A source outside any group can run at its own time(s); otherwise it's manual-only. |
| **One-time run** | A source or group scheduled once at a given date/time; uploads right away. |
| **Run** | One execution for a set of sources; has a report and per-source results in 📊 History. |
| **Artifact** | What a source produces in a run; becomes one zip (or one plain copy). |
| **Archive** | `\\fileserver\Backuper\<Jalali date>\…` (or a local folder); keeps N days. |
| **Statuses** | ✅ ok · ⚠️ ok with warnings · 🟠 partial (stored somewhere, not everywhere) · ❌ failed · ⏭ skipped. |

## 5. Data and files

| Where | What |
|---|---|
| `.env` | Bootstrap settings (bot token, API id/hash, owners, `SECRET_KEY`, timezone, paths, proxy). `chmod 600`. |
| `data/backuper.db` | SQLite: settings, credentials (encrypted), sources, groups, one-time runs, run history, admins. |
| `data/telegram.session` | Telethon session of the bot. |
| `data/logs/backuper.log` | Rotating log (10 MB × 5); also in journald. |
| `data/staging/` | Temporary zips before archive/upload (`run-<id>`), `unsent/` (zips that reached nowhere), `resend/`. |
| `local/company.yaml` | The company's configuration for `backuper import` (git-ignored). |
| Archive | `<root>\<1405-07-22>\<Source>[_<item>]_<1405-07-22_0800>.zip` and `<Source>_<stamp>\` plain copies. |
| Telegram | Group bound with `/bind`; topics 🗄 Databases, 🌐 Network, 🌍 Websites, 📁 File Servers, 📊 Reports (or per source / none). |

Database tables are described in [code/backuper/db.md](code/backuper/db.md).

**Backing up the bot itself:** copy `.env` and `data/backuper.db` somewhere safe (both are needed: the
database's secrets can only be decrypted with `SECRET_KEY`).

## 6. Configuration

- **`.env`** — see [code/backuper/config.md](code/backuper/config.md).
- **⚙️ Settings** (in the bot, or `backuper set`) — archive location/credential/days, date-folder format,
  zip password, compression level, topic layout, Telegram part size, retries, parallel sources, notifications.
- **Sources, credentials, groups** — in the bot, or bulk with `backuper import file.yaml`
  ([example](../examples/sources.example.yaml)); `backuper export` writes the current setup (no passwords).
- **Passwords** are entered in the bot (the message is deleted) or with `backuper credential NAME`
  (typed hidden) — never stored in YAML or git.

## 7. Scheduling rules

1. A **group** runs all its enabled sources at its start time(s) (cron lines also possible, e.g. Saturdays).
2. If the group has **Upload from**, the run waits with its uploads until then.
3. A source **without a group** runs at its **own time**, or only manually.
4. **One-time runs** run once; if the service was down, they still start up to 1 hour late, else "missed".
5. **Manual and one-time runs upload as soon as they're done.**
6. **Runs may overlap**, but there is **one upload queue**: two files are never uploaded at the same
   time; a run waits for another run's uploads. A source already running elsewhere is skipped (⏭).
7. Times are the **bot server's clock** (synchronised with NTP), in `TIMEZONE` — servers with a wrong
   clock don't affect the schedule.

## 8. What gets deleted, when (retention)

| What | Rule | Safety |
|---|---|---|
| Old backups on SQL servers | "Keep last N" per database (collect: per group of files; run-BACKUP: `mine` = only the bot's files, `all` = also the application's `<db>_*.bak`) | Only after the new backup is safely in the archive (or Telegram). Never the newest file. |
| Archive date folders | Keep the newest N (default 14) | Only folders named like a Jalali date. |
| Per-source archive copies | "Archive copies to keep" | Never a file that also belongs to a kept copy. |
| Hosting panel archives | `no`: deleted after download (even if the download failed); `newest`: the new one stays, older deleted after download; `replace`: older deleted *before* the new backup, the new one stays | — |
| Router files | Export/backup files deleted from the router after download | — |
| Staging | Run folders deleted after each run; `unsent/` after the archive retention period | Zips that reached nowhere are kept. |

## 9. Security model

- **Secrets** (passwords, tokens, zip password) are encrypted in the database (Fernet, `SECRET_KEY` in `.env`),
  decrypted only in memory, never logged; SSH error messages mask `password=…`; password messages typed in the
  bot are deleted; the export command never writes passwords.
- **Zips are AES-256** (zip password set in ⚙️ Settings — keep a copy outside Telegram). Plain copies
  (Edari/Finance) stay on the internal file server and are not encrypted by design.
- **Least privilege:** the bot uses a normal domain user (not an admin) with rights only on the folders it
  needs; SQL login with `db_backupoperator` only; MikroTik user with only the needed policies and restricted
  to the bot server's IP; hosting via API token + FTP account.
- **SSH host keys are pinned** on first use; a changed key stops the backup until an admin confirms.
- **TLS certificates are verified** for hosting panels/FTPS (use the certificate's host name).
- **Admin panel:** only owners (`OWNER_IDS`) and admins they add; everyone else is ignored (told their id on `/start`).
- **Nothing is pushed into the company network from outside**: everything is pulled by the bot server.

## 10. Operations

| Task | How |
|---|---|
| Install / upgrade | `git pull && ./install.sh` (keeps `.env` and `data/`), then `backuper restart`. See [SETUP.md](SETUP.md). |
| Restart | bot 🔄 Restart / `/restart`, or `backuper restart [--now]` — waits for running backups unless "now"; no sudo ([control](code/backuper/control.md)). |
| Start / stop / status | `sudo systemctl start|stop|status backuper` |
| Logs | per run: bot 📄 Log / `backuper log [id]`; service: `journalctl -u backuper -f`, `data/logs/backuper.log` |
| What's running now? | `E:\Backuper\_backuper-status.txt` (open in Explorer), `backuper status`, the bot's `/status`, the live message in 📊 Reports |
| Run now | bot ▶️ (all / a group / a source) or `backuper run [Name…]` |
| Check config | `backuper check`, `backuper test [Name…]`, `backuper list` |
| Move to another company | clone + `./install.sh` + new bot + `backuper import company.yaml` + passwords. |
| Restore | open the zip with 7-Zip/WinRAR (parts: put all `.zip.0NN` in one folder, open `.001`), then restore the `.bak` / `.backup` / `.rsc` / panel archive as described in [SETUP.md](SETUP.md#restoring). |

## 11. Troubleshooting

**Start with the run's log:** 📊 History → the run → **📄 Log** (or `backuper log` on the server). Every
line is tagged `[run#N Source]`; it shows each step with sizes, durations and speeds, the exact error and a
traceback for unexpected ones. The service log (`journalctl -u backuper -f`, `data/logs/backuper.log`)
covers everything outside runs (startup, schedule, bot). See [code/backuper/logctx.md](code/backuper/logctx.md).

| Symptom | Likely cause / fix |
|---|---|
| Bot doesn't answer | not an owner/admin (`/id`), service down (`systemctl status`), or archive-only mode (no API id). |
| `Request requires 1 credits but only 0 credits are available` | SMB flow control on a shared connection — fixed in 1.7.0 (one connection per transfer, waiting for credits). If it ever shows again, it's a library change: see [transport/smb](code/backuper/transport/smb.md). |
| `*.uploading` / `*.partial` in a date folder | an unfinished zip/copy: being written right now (see the status file), or left by an interrupted run — removed at the next service start. |
| `STATUS_ACCESS_DENIED` | share or NTFS rights missing for the bot account ([script](../docs/windows/setup-backuper-access.ps1)). |
| SQL "Login failed … disabled" | vendor-locked server → [mssql/03](mssql/03-locked-out-of-sql.md). |
| SQL "TDS server connection failed" | old SQL Server without TLS 1.2 → *Connection encryption* = off. |
| SQL port closed but test passes | named instance on another port, found through SQL Browser (UDP 1434). |
| "newest backup is 40h old" | the job/app on that server stopped making backups. |
| Router "Permission denied" | user/password, or the user's allowed address doesn't match the IP the router sees (check the router log). |
| Router "connection refused" | SSH service disabled or its *Available From* list doesn't include the bot server. |
| Hosting: no backup found | FTP account not in the home folder; wrong panel host (CDN in front of the domain). |
| Backups keep growing | an old backup file inside the hosting account gets included in each new one. |
| Topics not created | enable Topics in the group, bot needs *Manage topics*, ⚙️ ♻️ Reset topics. |

## 12. Extending

**New source type:** create `backuper/sources/<x>.py`:

```python
class MyThing(SourceType):
    type_id = "my_thing"; title = "My thing"; category = "files"
    fields = [Field("host", "Host", HOST), Field("credential", "Login", CREDENTIAL)]

    async def collect(self) -> list[Artifact]:
        ...download/prepare into self.ctx.workdir...
        return [Artifact(label="", entries=[Entry.from_path(p)], cleanup=[p])]

    async def test(self) -> str: ...            # optional
    async def after_success(self, artifacts): ...  # optional (retention)
```

Register it in `sources/__init__.py`, add `docs/code/backuper/sources/<x>.md` (the docs test insists),
tests, a CHANGELOG entry, and the requirements in [SOURCES.md](SOURCES.md). The bot, YAML import/export and
scheduling work automatically.

**New output (e.g. S3):** follow `destinations/archive.py`; call it from `engine._package` /
`engine._finish_source`.

## 13. Tests

`.venv/bin/pytest` — 43 tests in ~7 s, no network: engine end-to-end with fake sources and a fake
Telegram, the admin panel driven by fake Telegram events, packaging/encryption, scheduling, parsing, and the
documentation guard. Real behaviour was verified on the first site (see [LESSONS.md](LESSONS.md)).
Details per test: [code/tests/README.md](code/tests/README.md).

## 14. Repository layout

```
backuper/            the application (see code/README.md)
tests/               pytest suite
docs/                PROJECT (this), SETUP, SOURCES, DECISIONS, LESSONS, code/ (per file),
                     windows/ mssql/ mikrotik/ helper scripts
deploy/              systemd unit template
examples/            generic YAML configuration
install.sh           installer/upgrader
CHANGELOG.md         versions
local/ data/ .env    company-specific — git-ignored, never committed
```

## 15. Known limitations / ideas

- Telegram's MTProto needs an `api_id`/`api_hash` from my.telegram.org; without it the service runs
  archive-only. A Bot-API fallback (50 MB parts) is possible but impractical for multi-GB backups.
- Very large Windows folders are copied in full each run (no incremental mode yet).
- Listing huge folder trees over slow links takes minutes (one round trip per folder).
- One bot = one Telegram group.
