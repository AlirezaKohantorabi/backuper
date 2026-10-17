# backuper/sources/self_backup.py

**Purpose:** source type `self` — *This Backuper server*. Backs up what's needed to rebuild the bot server:

| In the zip | Why |
|---|---|
| `data/backuper.db` | every source, group, setting, history and (encrypted) password — a consistent copy made with SQLite's online backup while the service keeps running |
| `.env` | `SECRET_KEY` (the only key that decrypts the database's passwords), bot settings, timezone |
| `local/*` | the company configuration (`company.yaml`) — git-ignored, so it exists only on this server |
| `RESTORE.txt` | version, git commit and the restore steps |

The zip is AES-encrypted with the zip password like every other backup. That password is itself stored inside
this backup, so **keep a copy of the zip password somewhere else** (password manager, sealed envelope).

**Used by:** [engine.py](../engine.md) (gets the paths from `RunContext.app`). Registered in
[sources/__init__.py](__init__.md).

| Name | What it does |
|---|---|
| `APP_DIR` | The installation folder (where `local/` is). |
| `RESTORE` | The text of `RESTORE.txt`. |
| `snapshot_database` | Consistent copy of the live SQLite database (`sqlite3` backup API). |
| `_commit` | Current git commit, for `RESTORE.txt`. |
| `SelfBackup` | The source type (category `system`, no settings of its own). |
| `SelfBackup.describe` | "database, .env, local/*.yaml". |
| `SelfBackup._files` | `.env` and every file in `local/`. |
| `SelfBackup.test` | Lists what would be backed up, with sizes. |
| `SelfBackup.collect` | Snapshot + RESTORE.txt in the source's work folder, then one artifact with everything. |
