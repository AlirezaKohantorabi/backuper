# Scripts and non-Python files

## `install.sh`

Installer/upgrader, run from the repository folder on the bot server.

| Step | What and why |
|---|---|
| 1. `uv` | Installs the `uv` package manager into `~/.local/bin` if missing. **Why uv:** works without root and without the `python3-venv`/`pip` system packages (a fresh Ubuntu server had neither), and can even download Python itself. |
| 2. venv + deps | `.venv` with Python 3.12 and the package (`pip install -e .`). |
| 3. `.env` | Created from `.env.example` with a fresh `SECRET_KEY` (`chmod 600`) — only if it doesn't exist. |
| 4. systemd | Writes `/etc/systemd/system/backuper.service` from `deploy/backuper.service` (user and folder filled in), enables it, and starts it if `.env` has a bot token and API id. Needs sudo once. `--no-service` skips this. |

Run it again after `git pull` to upgrade (it keeps `.env` and `data/`).

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

## `docs/windows/verify-backup.ps1`

After restoring by hand: extract a zip with 7-Zip/WinRAR, then
`.\verify-backup.ps1 <name>.zip.sha256 [<extracted folder>]` hashes every extracted file and compares it with
the SHA-256 taken before zipping. Prints MISSING / DIFFERENT lines, or "All N file(s) are identical"; exit
code 1 on a problem. Handles paths longer than 260 characters; Windows PowerShell 5.1 or PowerShell 7.

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
