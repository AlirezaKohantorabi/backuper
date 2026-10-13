# backuper/sources/mssql_trigger.py

**Purpose:** source type **"SQL Server — run BACKUP + collect"**: the bot connects to SQL Server, runs
`BACKUP DATABASE … TO DISK = '<server folder>\<db>_<1405-07-21_0800>.bak' WITH COPY_ONLY, CHECKSUM
[, COMPRESSION]`, then reads the file from the share pointing to that same folder.

**Why this is the preferred way:** the backup no longer depends on a job (or a person closing an app)
on each server; adding a database is a bot setting, "Run now" makes a fresh backup, and it works on
SQL Server Express (no SQL Agent). `COPY_ONLY` keeps existing differential/log backup chains intact.

**Uses:** `pymssql` (FreeTDS, no ODBC driver needed), [mssql_common.py](mssql_common.md) (retention),
[base.py](base.md), SMB.

## Fields

`host`, `port`, `instance` (named instances are found through SQL Browser, UDP 1434), `sql_credential`
(SQL login or `DOMAIN\user`), `databases` (`*` = all online user databases), `server_dir` (path **on the SQL
server**), `share` (UNC to the same folder), `share_credential`, `copy_only`, `compression`
(auto/on/off), `keep_last`, `retention_scope` (mine/all), `encryption` (auto/off/require),
`timeout_minutes` (+ common fields).

## Functions and methods

| Name | What it does / why |
|---|---|
| `SYSTEM_DBS` | master, model, msdb, tempdb — never included by `*`. |
| `quote_ident` | `[name]` with `]` escaped — database names can't be bound as parameters in `BACKUP`. |
| `MSSQLTrigger` | The source type. |
| `MSSQLTrigger.describe` | `host[\instance] · databases`. |
| `MSSQLTrigger._connect` | Connects (autocommit, long query timeout); `encryption=off` for SQL 2008/2012 that only speak TLS 1.0 (OpenSSL refuses it); readable hint on "TDS server connection failed". |
| `MSSQLTrigger._list_dbs` | Online databases (no snapshots). |
| `MSSQLTrigger._resolve_dbs` / `MSSQLTrigger._resolve_dbs_sync` | Configured names → exact names (case-insensitive), clear error for missing ones; `*` expands to all user databases. |
| `MSSQLTrigger._compress` | `auto`: compression only where the edition supports it (Enterprise/Standard/Developer; not Express). Compressed backups are ~5× smaller (real case: 5.3 GB → 0.98 GB, run 4.5 min → 1.5 min). |
| `MSSQLTrigger._backup_sync` | Runs `BACKUP DATABASE` and drains all result sets. |
| `MSSQLTrigger.test` | Version/edition, user databases, which ones will be backed up, compression on/off, share readable. |
| `MSSQLTrigger.collect` | Back up each database, check the file is visible on the share, return Artifacts read straight from the share. |
| `MSSQLTrigger.after_success` | *Keep last N* per database: `mine` = only `<db>_<yyyy-mm-dd>_<hhmm>.bak` files this source made; `all` = every `<db>_*.bak` in the folder (also the application's own backups). |
| `_sql_err` | Cleans FreeTDS error text ("DB-Lib error…" noise). |

**Requirements on the SQL server** (see [docs/mssql](../../../mssql/01-create-backup-login.sql)): a login
with `db_backupoperator`; TCP reachable from the bot server; the SQL Server **service** may write to
`server_dir`; the bot's account may read and delete in the share.
