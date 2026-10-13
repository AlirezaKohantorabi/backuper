# backuper/sources/mssql_collect.py

**Purpose:** source type **"SQL Server — collect .bak from share"**: a job on the SQL server (SQL Agent,
maintenance plan, or the application itself) already writes backups into a folder; the bot picks the
newest file per group from the share and, after it's safely stored, deletes old ones beyond *Keep last N*.

**When to use:** the server already makes backups automatically (iCan's ICANSBPMS), or SQL access isn't
available yet (Hamrahan System servers until the vendor creates a login).

**Uses:** [mssql_common.py](mssql_common.md), [base.py](base.md), SMB via `RunContext.smb`.

## Fields

`share` (UNC), `credential`, `patterns`, `group_by` (pattern/database/folder), `recursive`,
`keep_last`, `max_age_hours` (+ common fields).

## Class and methods

| Name | What it does |
|---|---|
| `MSSQLCollect` | The source type (`type_id = "mssql_collect"`, category databases). |
| `MSSQLCollect.describe` | `share · keep N`. |
| `MSSQLCollect._smb` | SMB client for the configured credential. |
| `MSSQLCollect._groups` | Lists the share (optionally recursive) and groups the files. |
| `MSSQLCollect.test` | Shows which file of each group would be sent. |
| `MSSQLCollect.collect` | Per group: freshness check; skip with a note if the newest file was already delivered (e.g. no backup on Thursday/Friday); wait while it's being written; return it as an Artifact (read straight from the share while zipping). |
| `MSSQLCollect.after_success` | Remember delivered files (only if they reached everywhere — a failed Telegram upload is retried next run) and apply *Keep last N* per group. |
