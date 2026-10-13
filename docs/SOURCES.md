# Source types and what they need

## When sources run: groups

- **Backup group** — a group (🗂 Groups in the bot) has a start time, e.g. 08:00. At that time
  it backs up all its sources, then uploads their files to Telegram **smallest first, one after
  another**. You never have to space times out: the last file may go out at 10:00 — that's
  decided by the sizes, not by you.
- **Own time** — a source without a group can run by itself, e.g. 19:00.
- No group and no own time = manual only (▶️ Run now).
- Runs may overlap (a 19:00 source while the 08:00 group is still uploading), but **two files
  are never uploaded at the same time**: a run waits for the other run's uploads to finish.
  A source that's already running is skipped by the second run. Archive-only sources in a group
  don't hold its uploads back.

Every source also has optional advanced fields (✏️ Edit after creation):

- **Send to Telegram** — off = archive only. The zip is then streamed straight into the
  archive share (no temporary copy on the bot server), for folders too big for Telegram.
- **Archive copies to keep** — keep only the newest N zips of this source in the archive
  (0 = the global "Archive days to keep").
- **Telegram topic** — post to a specific topic.

## Access on Windows servers

Give the bot its own **non-admin** account instead of the Administrator password:
[windows/setup-backuper-access.ps1](windows/setup-backuper-access.ps1) creates a local user
`backuper` (same password on every server → one credential in the bot), a hidden share for
the folder and the NTFS rights (Modify, or Read with `-ReadOnly`). With `-SqlBackups` it also
lets SQL Server write there, with `-OpenSqlPort` it opens TCP 1433 for the bot server only.
In an AD domain you can pass a domain account instead (`-User CORP\svc-backuper`).

## SQL Server — collect .bak from share (`mssql_collect`)

A SQL Agent job / maintenance plan writes `.bak` files into a folder; the bot picks the
newest file per database from the share.

- **On the SQL server**: a backup job (template:
  [mssql/02-create-agent-backup-job.sql](mssql/02-create-agent-backup-job.sql)), and the
  backup folder shared.
- **Account**: read on the share; **Modify** (delete) if *Keep last N* > 0.
- **One backup per** — `pattern` (newest file of each pattern), `database` (newest file per
  database name in the file names, e.g. `Holoo1_…` / `Holoo2_…`), or `folder` (newest file per
  sub-folder, e.g. `Arpa\` and `ArpaEn\` — turn on *Include sub-folders*). *Keep last N*
  counts each group separately.
- **Max age**: if the newest file is older than this (default 26 h), the run reports a failure —
  the job on the server is probably broken.
- A file that's still being written is waited for (up to 1 h).
- A file that was already delivered isn't sent again: the run shows ⏭ *no new backup since …*
  (e.g. applications that only back up on working days). If its Telegram upload failed, it's
  retried automatically on the next run.
- Files that don't compress (SQL backups made `WITH COMPRESSION`, `.zip`, media…) are stored in
  the zip as-is — encrypted, but not re-compressed, which saves a lot of CPU time.

## SQL Server — run BACKUP + collect (`mssql_trigger`)

The bot connects to SQL Server, runs `BACKUP DATABASE … WITH COPY_ONLY, CHECKSUM` into a
folder on the server, then collects the file over SMB. No Agent job needed; works on
Express; "Run now" in the bot produces a fresh backup.

- **Locked out of SQL Server** ("Login failed … account is disabled")? See
  [mssql/03-locked-out-of-sql.md](mssql/03-locked-out-of-sql.md).
- **On the SQL server**: [mssql/01-create-backup-login.sql](mssql/01-create-backup-login.sql)
  (SQL login + `db_backupoperator`), mixed-mode auth (or use a domain account), TCP 1433 /
  the instance port open to the backup server, the SQL **service** account can write to
  the backup folder, the folder shared for the bot.
- `COPY_ONLY` (default) keeps your existing differential/log chains intact.
- *Keep last N counts*: `mine` (default) only deletes files this source created
  (`<db>_<date>_<time>.bak`); `all` also prunes the application's own backups of that database
  in the folder (every `<db>_*.bak`).
- Named instances: set *Instance name* (port found via SQL Browser, UDP 1434).
- Very old servers (SQL 2008 / 2012 without TLS 1.2): set *Connection encryption* = `off`.

## MikroTik (`mikrotik`)

Over SSH: `/export show-sensitive file=…` (`.rsc`) and `/system backup save` (`.backup`),
downloaded over SFTP, then deleted from the router. One zip per router containing both
plus `router-info.txt`.

- **On the router**: [mikrotik/create-backup-user.rsc](mikrotik/create-backup-user.rsc)
  — user group `ssh,ftp,read,write,policy,test,sensitive`, user restricted to the backup
  server's IP, SSH reachable from the backup server.
- The `.backup` is unencrypted by default (the zip is AES-256); set *Backup file password*
  to encrypt it too.
- The SSH host key is pinned on first connection; a changed key stops the backup until you
  press *Forget pinned SSH key*.

## Windows folder (`smb_folder`)

Zips a whole folder from a share, streamed straight into the zip (no temp copy).

- Path like `\\server\Share\Folder` or an admin share `\\server\D$\Folder` (needs an
  administrator account).
- Files locked by another program are skipped and listed as warnings (*Skip locked files*).
- *Exclude patterns*: `~$*`, `*.tmp`, `Thumbs.db`… by default.

## Folder over SFTP / FTP / FTPS (`remote_folder`)

Downloads a remote folder recursively (Linux servers, NAS, hosting). *Delete remote files
after* turns it into a "move" (e.g. for servers that drop their own dumps somewhere).

## cPanel (`cpanel`)

1. calls `Backup::fullbackup_to_homedir` (UAPI),
2. waits until `Backups::listfullbackups` reports it complete,
3. downloads `backup-….tar.gz` over FTPS/SFTP/FTP,
4. deletes it from the account (even if the download failed).

- **Login**: cPanel username + **API token** (cPanel → Security → Manage API Tokens) with
  *Login type* `token`, plus a separate *Download login* (the cPanel FTP account) — or use the
  account password for both with *Login type* `password`.
- *Include home directory* off = DBs/email/config only.
- The FTP account must start in the **home folder** (`/home/<user>`), not `public_html/...`:
  that's where cPanel writes the backup. (An existing account's folder can't be changed in
  cPanel — delete and recreate it with `/` as directory.)
- *Download host*: shared hosts often have an FTP certificate for the server's own name
  (e.g. `box402.parsvds.com`); put it here to keep certificate checks on.

## DirectAdmin (`directadmin`)

1. `CMD_API_SITE_BACKUP` (`action=backup`, all items by default),
2. waits for `~/backups/backup-…tar.gz` (or `.tar.zst`) to appear and stop growing,
3. downloads it over FTPS/SFTP/FTP, deletes it from the host.

- **Login**: user + password or a **Login Key** that allows `CMD_API_SITE_BACKUP` and
  `CMD_API_SHOW_DOMAINS`. *Download login*: the main FTP account (home = `/home/<user>`).
- *Domain*: any domain of the account (required by the API).
