# Scripts and non-Python files

## `install.sh`

Installer/upgrader, run from the repository folder on the bot server.

| Step | What and why |
|---|---|
| 1. `uv` | Installs the `uv` package manager into `~/.local/bin` if missing. **Why uv:** works without root and without the `python3-venv`/`pip` system packages (a fresh Ubuntu server had neither), and can even download Python itself. |
| 2. venv + deps | `.venv` with Python 3.12 and the package (`pip install -e .`). |
| 2b. rclone | A pinned rclone release (for the [cloud copy](backuper/destinations/cloud.md)) downloaded from rclone.org, checked against its `SHA256SUMS`, installed as `.venv/bin/rclone` (no root needed; the service finds it next to its Python). |
| 2c. `.venv-zk` | A separate venv with `pyzk==0.9` for the [ZKTeco kit](backuper/sources/zkteco.md) (its read-only guard depends on that exact version). |
| 3. `.env` | Created from `.env.example` with a fresh `SECRET_KEY` (`chmod 600`) — only if it doesn't exist. |
| 4. systemd | Writes `/etc/systemd/system/backuper.service` from `deploy/backuper.service` (user and folder filled in), enables it, and starts it if `.env` has a bot token and API id. Needs sudo once. `--no-service` skips this. |

Run it again after `git pull` to upgrade (it keeps `.env` and `data/`).

## `vendor/zk-backup/`

The owner's ZKTeco VF600 kit, kept **unchanged** (its invariants: pyzk 0.9 pinned, no write command in the
backup's whitelist, `zk_restore.py --write` never automated, `last_run.json` only extended). Its
[README](../../vendor/zk-backup/README.md) documents everything (protocol, both tools, runbook, findings); its
[CHANGELOG](../../vendor/zk-backup/CHANGELOG.md) lists the versions.

**Its own repository** is [zk-backup](https://github.com/AlirezaKohantorabi/zk-backup) (private); this folder is a **git subtree** of it (decision
D31). Never edit the files here — change the kit there, run its tests, tag it, then bring it in:

```bash
git subtree pull --prefix=vendor/zk-backup https://github.com/AlirezaKohantorabi/zk-backup.git main --squash -m "ZKTeco kit X.Y"
```

then run the kit's tests here (below) and Backuper's `tests/test_zkteco.py`, and note the kit version in
CHANGELOG.md. `git log -1 --grep=git-subtree-dir:.vendor/zk-backup` shows which kit commit is inside.
Version inside since Backuper 1.11.2: **2.1**.

| File | Role here |
|---|---|
| `zk_backup.py` | Run by the [zkteco source](backuper/sources/zkteco.md) for each device. |
| `zk_restore.py` | Restore onto a device — **only by a person**, from an admin machine; never run by the bot. |
| `install.sh` | The kit's own systemd timer installer — **not used** (the bot schedules the backup; running both would read the device twice). |
| `CHANGELOG.md`, `.gitignore` | The kit's versions; keeps backup output (CSV/JSON, SHA256SUMS) out of git. |
| `tests/run_tests.sh`, `tests/fake_zk.py`, `tests/fake_spare.py` | The kit's 25 scenarios against simulated devices: `PY=.venv-zk/bin/python vendor/zk-backup/tests/run_tests.sh` — must pass after any change to the kit (25/25 on 2026-10-31). |

## `deploy/backuper.service`

systemd unit template (`@USER@`, `@DIR@` are filled in by `install.sh`): runs
`.venv/bin/backuper --env .env serve`, restarts after 15 s on failure, `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=full`. Logs go to journald (`journalctl -u backuper -f`) and `data/logs/backuper.log`.

## `docs/windows/setup-backuper-access.ps1`

PowerShell, run as administrator on a Windows server, once per folder. Gives the bot's account
**least-privilege** access: creates a local user if needed (skipped for domain accounts), a hidden share
(or grants on an existing share — also one pointing at a parent folder), NTFS Modify (or Read with
`-ReadOnly`), `-SqlBackups` lets the SQL Server service write there, `-OpenSqlPort` opens TCP 1433 for the
bot server only. Prints the resulting `\\server\share` paths.
Per-server one-liners used at the first site are in [../LESSONS.md](../LESSONS.md#permissions).

## `docs/mssql/01-create-backup-login.sql`

Creates the SQL login the bot uses for "run BACKUP" sources (Windows/domain account by default, or a SQL
login with a password) and grants `db_backupoperator` in every online user database. `USE [master]` is
correct: logins are server-wide. Re-run after adding databases.

## `docs/mssql/02-create-agent-backup-job.sql`

Optional: a SQL Agent job writing one full backup per database per day as
`<db>_backup_YYYY_MM_DD_HHMMSS.bak` (for "collect" sources). Not needed when the bot runs the backups itself.

## `docs/mssql/03-locked-out-of-sql.md`

What to do when SQL Server says "Login failed … account is disabled" (vendor-locked servers): ask the vendor,
or Microsoft's single-user-mode procedure.

## `docs/mikrotik/create-backup-user.rsc`

RouterOS commands: a `backuper` user group with exactly `ssh,ftp,read,write,policy,test,sensitive`, a user
restricted to the bot server's IP, SSH enabled, optional firewall rule and NTP lines.

## `examples/sources.example.yaml`

A generic company configuration (groups, credentials without passwords, one source of each kind) for
`backuper import`. The real company file lives in `local/` (git-ignored).

## `.env.example`

Template for `.env` with every bootstrap key explained (see [backuper/config.md](backuper/config.md)).
