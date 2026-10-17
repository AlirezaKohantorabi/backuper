# Setup & deploy guide

From an empty server to working backups. Takes about an hour for a new company (most of it is preparing
access on the servers). The overview of how everything works is in [PROJECT.md](PROJECT.md).

## 0. What you need

- A Linux server (Ubuntu 22.04+/Debian 12+) that can reach everything you want to back up and the
  internet (Telegram, hosting panels). 2+ cores, 4+ GB RAM, free disk for one day's zips (`STAGING_DIR`).
- A Telegram account (for the bot and the group).
- Admin access to each system once (to give the bot its own limited account).

## 1. Install the service

```bash
git clone https://github.com/<you>/backuper.git ~/backuper-app
cd ~/backuper-app
./install.sh          # Python venv, dependencies, .env with a new SECRET_KEY, systemd service (sudo once)
```

Make sure the server's clock is right — schedules and the Jalali dates use it:
```bash
timedatectl            # "System clock synchronized: yes"; if not: sudo apt install -y chrony
sudo timedatectl set-timezone Asia/Tehran      # optional: logs in local time
```

## 2. Telegram

1. **Bot:** [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token into `.env` → `BOT_TOKEN=`.
2. **api_id / api_hash:** <https://my.telegram.org> → *API development tools* → create an app (title e.g.
   "Company Backup", short name letters/digits only, platform Desktop, a short description) → copy both into
   `.env`. They let the bot upload 2 GB per file instead of 50 MB.
   *If my.telegram.org only answers "ERROR":* that's on Telegram's side; the service still runs in
   **archive-only mode** (backups go to the archive, no uploads, no panel) until you add them.
3. **Your Telegram id:** send `/start` to [@userinfobot](https://t.me/userinfobot) → `.env` → `OWNER_IDS=`.
4. **Group:** create a group, turn on *Topics*, add the bot as **admin** (post, delete, manage topics).
5. `sudo systemctl restart backuper`, then in the group send **`/bind`** and in a private chat with the
   bot send **`/menu`**.

## 3. Give the bot access to your systems

### Windows file shares (SQL backup folders, file server folders, the archive)

Use one **normal** account for the bot — a domain user if the servers are in a domain (e.g.
`CORP\BackuperBot`), otherwise the same local user on each server. **Never an administrator.**

For each folder, give that account rights on **both** the share and the folder (NTFS):

| Folder | Rights |
|---|---|
| SQL backup folders | Modify (read + delete old backups) |
| Folders the bot runs `BACKUP` into | Modify for the bot, **and** Modify for the SQL Server service (`NT SERVICE\MSSQLSERVER` or `NT SERVICE\MSSQL$<INSTANCE>`) |
| Folders that are only read (e.g. file-server data) | Read |
| The archive folder | Modify |

The script [windows/setup-backuper-access.ps1](windows/setup-backuper-access.ps1) does it (run as
administrator, once per folder), or use the short per-server one-liners in
[LESSONS.md → Permissions](LESSONS.md#permissions).

Then store the account on the bot server (password typed hidden):
```bash
~/backuper-app/.venv/bin/backuper --env ~/backuper-app/.env credential backuper -u CORP\\BackuperBot
```

### SQL Server

- **Server already makes backups** (SQL Agent job, maintenance plan, or the application) → source type
  *collect*; the share above is enough.
- **Otherwise the bot makes them** (recommended — independent of jobs and people) → run
  [mssql/01-create-backup-login.sql](mssql/01-create-backup-login.sql) in SSMS as sysadmin (domain account
  login by default, or a SQL login + password), and make sure TCP is reachable from the bot server
  (named instances: SQL Browser, UDP 1434). Locked out? → [mssql/03-locked-out-of-sql.md](mssql/03-locked-out-of-sql.md).

### MikroTik

Paste [mikrotik/create-backup-user.rsc](mikrotik/create-backup-user.rsc) into each router (same password on
all), allow the bot server to reach SSH (service *Available From* + firewall), then:
```bash
~/backuper-app/.venv/bin/backuper --env ~/backuper-app/.env credential mikrotik-bot -u backuper
```

### cPanel

- cPanel → *Security → Manage API Tokens* → create a token; note the **cPanel username** (right sidebar).
- cPanel → *FTP Accounts* → an account whose **Directory is the home folder** (`/`), not `public_html/…`.
- In the source, *Download host* = the FTP certificate's name (e.g. `box402.parsvds.com`).

### DirectAdmin

- The **real panel address** (from the browser bar inside DirectAdmin, e.g. `serverXX.host.com:2223`) —
  not the website domain if it's behind a CDN.
- DirectAdmin username + password (*Change Password*: tick DirectAdmin and FTP, not the database).

## 4. Configure the backups

Either in the bot (📦 Sources → ➕ Add source, 🗂 Groups, ⚙️ Settings) or in bulk:

```bash
cp examples/sources.example.yaml local/company.yaml   # edit it
.venv/bin/backuper import local/company.yaml --dry-run
.venv/bin/backuper import local/company.yaml
.venv/bin/backuper test                                # every source
.venv/bin/backuper check                               # archive, zip password, timezone…
sudo systemctl restart backuper
```

In ⚙️ Settings set at least: **zip password** (keep a copy outside Telegram), **archive location** +
**archive credential**, archive days to keep.

First full run: bot → ▶️ Run all now (or `backuper run`). Check 📊 History and the archive folder.

## 5. Upgrading

```bash
cd ~/backuper-app && git pull && ./install.sh && backuper restart
```
`backuper restart` (or 🔄 Restart in the bot) waits until no backup is running, then the service restarts
itself — no sudo needed (`backuper restart --now` doesn't wait). Database changes are applied automatically at
startup. Read [CHANGELOG.md](../CHANGELOG.md) first.

`install.sh` links `backuper` into `~/.local/bin`, so the command works from any folder (log in again once
if your shell says *command not found*).

## 6. Moving to another company

Clone, `./install.sh`, a new bot and group, new `.env`, then `backuper import` the new company's YAML and
enter the passwords. Nothing company-specific is in git (`.env`, `data/`, `local/` are ignored).

## 7. Backing up / moving the bot itself

Copy `.env` **and** `data/backuper.db` (the database's passwords can only be decrypted with the
`SECRET_KEY` in `.env`). Optional: `data/telegram.session` (avoids a new login).

## 8. Uninstall

```bash
sudo systemctl disable --now backuper && sudo rm /etc/systemd/system/backuper.service && rm -rf ~/backuper-app
```

## Restoring

- **Zip:** 7-Zip or WinRAR + the zip password. Split uploads: put all `.zip.001`, `.zip.002`… in one folder
  and open `.001`.
- **Is a zip intact?** Every zip was unzipped and compared file by file (SHA-256) right after it was made. Later:
  7-Zip → *Test archive*, or on the server `backuper verify [DATE] [ZIP…]` (the zip's own AES/CRC checks).
- **The bot server itself:** the daily `Backuper_<date>.zip` holds `.env`, `data/backuper.db` and `local/`;
  follow its `RESTORE.txt` (clone, `./install.sh`, copy the three back, restart). Needs the zip password.
- **SQL:** SSMS → Restore Database (or `RESTORE DATABASE … FROM DISK = '…bak' WITH MOVE …`).
- **MikroTik:** `.backup` → upload to *Files*, `/system backup load` (same model only); `.rsc` →
  `/import file-name=…` (portable — review it first; it contains passwords).
- **cPanel:** WHM *Transfer or Restore a cPanel Account*, or extract `homedir/` and `mysql/*.sql` by hand.
  **DirectAdmin:** put the file into `~/backups/` → *Create/Restore Backups*.
- **Plain copies** (Edari/Finance): just copy the files back from `E:\Backuper\<date>\<Name>_<stamp>\`.
