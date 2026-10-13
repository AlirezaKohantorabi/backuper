# backuper/sources/mssql_common.py

**Purpose:** logic shared by both SQL Server source types for `.bak` (or other backup) files sitting on a
share: which files belong together, which is newest, is it fresh, is it still being written, and
deleting old ones safely.

**Used by:** [mssql_collect.py](mssql_collect.md), [mssql_trigger.py](mssql_trigger.md) (`apply_retention`).

| Name | What it does / why |
|---|---|
| `_DATE_TOKEN`, `_NOISE_SUFFIX` | Patterns to find the date part and words like `_backup`/`_FULL` in file names. |
| `GROUP_MODES` | `pattern`, `database`, `folder` (see `group_files`). |
| `infer_db_name` | Database name from common naming schemes: maintenance plan (`Sales_backup_2026_10_07_…`), the bot's own (`Sales_1405-07-15_0300`), Ola Hallengren. `None` if no date is found. |
| `_matches` | Pattern match against the file name, or the path below the share if the pattern contains `/`. |
| `group_files` | Groups candidate files; each group yields one backup (its newest file) and has its own "keep last N". `pattern`: one group per pattern (e.g. `Holoo1_*.zip`, `Holoo2_*.zip`); `database`: by the name in the file; `folder`: by sub-folder (`Arpa\`, `ArpaEn\`). |
| `label_for` | Readable item name for a group (pattern prefix, folder or database name) — used in zip names. |
| `signature` | `name|size|mtime` — identifies a file so an unchanged newest file isn't sent twice. |
| `check_fresh` | Fails if the newest file is older than *Max age* — the job on the server probably stopped. |
| `wait_until_stable` | Waits (up to 1 h) while a file is still being written; `stat` opens a handle so the size is current. |
| `apply_retention` | Deletes all but the newest N files of a group (list must be newest first); errors become warnings. |
