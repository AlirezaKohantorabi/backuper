# Backuper

Collects backups from all over the network, zips each one with AES-256, keeps
them in a dated archive and ships them to a Telegram group. Everything is
managed from a Telegram admin panel: add servers, change schedules, run
backups, see history, re-send files.

```
 SQL Server ─┐ (SMB / T-SQL)
 MikroTik ───┤ (SSH + SFTP)            ┌─► Archive  \\server\Backuper\1405-07-15\*.zip  (keep N days)
 Win folders ┤ (SMB)       ─► zip ─────┤
 SFTP/FTP ───┤             AES-256     └─► Telegram group (topics) — smallest file first
 cPanel / DA ┘ (API + FTPS/SFTP)
```

## Features

| | |
|---|---|
| **Sources** | SQL Server (collect .bak from a share, or run `BACKUP DATABASE` itself), MikroTik (`export show-sensitive` + binary backup), Windows folders over SMB, folders over SFTP/FTP/FTPS, cPanel and DirectAdmin full-account backups |
| **Packaging** | One zip per backup item, AES-256 (7-Zip/WinRAR), streamed (no temp copy for SMB), already-compressed files stored; or a **plain dated folder copy** (server-side when on the archive's server) |
| **Archive** | Network share or local folder, one folder per **Jalali** date, keeps the newest N days |
| **Scheduling** | Backup **groups** with a start time and optional *upload from* time (uploads smallest → biggest, one at a time), sources at their own time, **one-time runs**, manual runs; overlapping runs never upload simultaneously; archive-only mode without Telegram |
| **Telegram** | MTProto uploads up to 2 GB per file (bigger zips are split into `.zip.001`, `.002`… which 7-Zip opens directly), forum topics per category or per source, run report + failure DMs |
| **Retention** | "Keep last N" on SQL servers — only after the new backup is safely stored. Hosting-panel archives are deleted from the host right after download |
| **Admin panel** | Sources, groups, credentials (encrypted), schedule, settings, run now, test connection, history, re-send from archive, admins |
| **Safety** | Secrets encrypted with Fernet, password messages deleted from the chat, SSH host keys pinned on first use, only listed Telegram IDs can use the panel |

## Quick start (new server)

```bash
git clone https://github.com/<you>/backuper.git && cd backuper
./install.sh                       # venv, deps, .env with a fresh SECRET_KEY, systemd service
nano .env                          # BOT_TOKEN, API_ID, API_HASH, OWNER_IDS
sudo systemctl restart backuper
```

Then in Telegram: open the bot → `/menu`. Add the bot to your group as admin
(with *Manage topics*), and send `/bind` in the group.

Full walkthrough (BotFather, my.telegram.org, group, shares, permissions):
**[docs/SETUP.md](docs/SETUP.md)**. What each source type needs on the other
side: **[docs/SOURCES.md](docs/SOURCES.md)**.

## Moving to another company

Everything company-specific lives in `.env` and `data/` (both git-ignored).
To set up a new site: clone, `./install.sh`, new bot from BotFather, then
either click sources together in the bot or bulk-load them:

```bash
.venv/bin/backuper import my-company.yaml   # see examples/sources.example.yaml
.venv/bin/backuper test                     # checks every source
```

`backuper export company.yaml` writes the current setup (without passwords)
— handy as documentation or as a template.

## Documentation

| Document | For |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | **Start here** — what it does, architecture, rules, security, operations |
| [docs/SETUP.md](docs/SETUP.md) | Install, configure, upgrade, move, restore |
| [docs/SOURCES.md](docs/SOURCES.md) | What each source type needs on the other side |
| [docs/code/](docs/code/README.md) | One page per code file: purpose, every function, connections |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Why things are built this way |
| [docs/LESSONS.md](docs/LESSONS.md) | What we learned: mistakes, measurements, methodology |
| [CHANGELOG.md](CHANGELOG.md) | Every version with date |

## CLI

```
backuper serve             # bot + scheduler (what the service runs)
backuper run [Name ...]    # one run now, from the shell
backuper test [Name ...]   # connectivity test
backuper check             # config, archive access, disk space
backuper credential NAME   # store a login, password typed hidden
backuper set KEY [VALUE]   # change a setting (secrets typed hidden)
backuper status            # what's running now, problems in the last runs, next runs
backuper log [RUN]         # a run's detailed log
backuper verify [DATE]     # unzip archived zips again and check them
backuper once WHEN NAME... # one-time run of sources/groups (--list, --cancel ID)
backuper restart [--now]   # restart the service (waits for running backups) — no sudo
backuper list | import FILE | export [FILE] | gen-key
```

## Adding a new kind of source

Create `backuper/sources/<thing>.py` with a `SourceType` subclass: declare its
`fields` (the bot builds the add/edit forms from them) and implement
`collect()` returning `Artifact`s (each becomes one zip). Optional: `test()`,
`after_success()` for retention. Register it in `backuper/sources/__init__.py` and add its page under
`docs/code/` (the test suite checks that every file and function is documented).

## Development

```bash
uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest
```
