# backuper/db.py

**Purpose:** all persistence, in one SQLite file (`data/backuper.db`). The data is small (tens of
sources, hundreds of runs), so it uses the standard `sqlite3` module synchronously, guarded by a lock,
in WAL mode. Secrets are encrypted by [crypto.py](crypto.md) on the way in and decrypted on the way out,
so the rest of the program only ever sees plaintext in memory.

**Used by:** [cli.py](cli.md), [bot/panel.py](bot/panel.md), [engine.py](engine.md),
[scheduler.py](scheduler.md), [sources/base.py](sources/base.md) (`RunContext` state and credentials),
[destinations/*](destinations/archive.md) (settings, Telegram topic ids).

## Tables

| Table | Holds |
|---|---|
| `meta` | Internal key/values: schema version, Telegram topic ids, one-time migration flags. |
| `settings` | ⚙️ Settings values (JSON). Secret ones encrypted. |
| `credentials` | Logins: name, username, encrypted secret, note. |
| `sources` | Name, type, enabled, `config` JSON (secret fields encrypted), `state` JSON (plugin memory). |
| `groups` | Backup groups: name, schedule, enabled, upload_at. |
| `one_offs` | One-time scheduled runs and their status; `retry` column (added automatically to older databases) marks automatic retries. |
| `runs` / `run_items` | History: one row per run, one row per source per run (status, message, files JSON for "Resend"). |
| `admins` | Extra admins added in the bot (owners come from `.env`). |

## Functions and methods

| Name | What it does / why |
|---|---|
| `SCHEMA_VERSION`, `SCHEMA` | Table definitions (`CREATE TABLE IF NOT EXISTS`). |
| `_now` | UTC timestamp string for created/updated columns. |
| `Store` | The database object (one per process). |
| `Store.__init__` | Opens the file, WAL mode, creates tables, adds columns added in later versions (`groups.upload_at`), runs `_migrate_main_schedule`. |
| `Store._migrate_main_schedule` | One-time: turns the v1 global schedule into a group "Daily" and puts ungrouped sources in it. Edits raw JSON so encrypted secrets stay untouched. |
| `Store.close` | Closes the connection. |
| `Store._q` / `Store._x` | Query (returns rows) / execute (returns last row id or row count), under the lock. |
| `Store._encrypt_config` / `Store._decrypt_config` | Encrypt the secret fields of a source config / decrypt every `enc:` value. |
| `Store.get_setting` / `Store.set_setting` / `Store.all_settings` | Settings with defaults from [settings.py](settings.md); secret settings encrypted. |
| `Store.get_json` / `Store.set_json` | Internal values in `meta` (e.g. Telegram topic ids). |
| `Store._cred` | Row → `Credential` (decrypted). |
| `Store.list_credentials` / `get_credential` / `get_credential_by_name` | Read credentials. |
| `Store.save_credential` | Create or update; `password=None` keeps the stored one. |
| `Store.rename_credential_refs` | After renaming a credential, update every source/setting that pointed to it. |
| `Store.delete_credential` | Delete (the bot refuses while it's still used). |
| `Store._src` | Row → `SourceRecord`. |
| `Store.list_sources` / `get_source` / `get_source_by_name` | Read sources (names are case-insensitive). |
| `Store.create_source` / `update_source` / `delete_source` | Write sources; `update_source` can also change name, enabled flag and type (YAML import). |
| `Store.set_source_state` | Save a plugin's memory (pinned SSH keys, last delivered file, archive history). |
| `Store._grp` | Row → `Group`. |
| `Store.list_groups` / `get_group` / `get_group_by_name` | Read groups. |
| `Store.save_group` | Create/update a group; renaming updates all member sources; `upload_at=None` keeps the stored value. |
| `Store.delete_group` | Delete; its sources become ungrouped. |
| `Store._set_group_refs` | Rewrites the `group` of every source pointing to a group name. |
| `Store.set_source_group` | Move one source into a group (or out with `""`). |
| `Store.group_members` | Sources in a group. |
| `Store._oneoff` | Row → `OneOff`. |
| `Store.add_one_off` / `list_one_offs` / `get_one_off` / `set_one_off` | One-time runs: create (optionally as retry no. N), list by status, read, change status (and record the run id). |
| `Store.create_run` / `finish_run` | Start/finish a history entry (status, total size, the report text). |
| `Store.add_run_item` / `update_run_item_files` | Per-source result of a run (files JSON is updated by "Resend"). |
| `Store.list_runs` / `get_run` / `run_items` / `get_run_item` | History pages. |
| `Store.last_item_for_source` | Last result of a source (shown on its page). |
| `Store.mark_stale_runs` | At startup: runs left "running" by a crash become "interrupted" (except runs still alive in another process). |
| `Store.prune_runs` | Keep only the newest 500 runs. |
| `Store.list_admins` / `add_admin` / `remove_admin` / `admin_ids` | Extra admins. |
