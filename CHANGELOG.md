# Changelog

Every version, what was added, changed, fixed and removed — with the date. Versions follow
[semantic versioning](https://semver.org): MAJOR for incompatible changes, MINOR for new features,
PATCH for fixes. Git tags `vX.Y.Z` point to the commit of each version.

The *why* behind the bigger changes is in [docs/DECISIONS.md](docs/DECISIONS.md); what we learned on
the way (mistakes included) is in [docs/LESSONS.md](docs/LESSONS.md).

---

## [1.8.0] — 2026-10-17

### Added
- **Integrity check of every zip.** While a file is zipped, its SHA-256 is taken from the same bytes; right after
  the zip is stored it is unzipped again (in memory) and every file is compared. A difference deletes the zip and
  makes it again. The hashes are saved next to the zip as `<name>.zip.sha256`; the report shows "✓ verified".
- `backuper verify [DATE] [ZIP…] [--all]` — re-check archived zips at any time (e.g. for disk damage).
- `docs/windows/verify-backup.ps1` — check the files after extracting a zip by hand on Windows.
- **Waiting for the archive:** if the archive server is off when a run starts, the run waits for it (up to 3 h)
  instead of failing every source. On 2026-10-17 the file server lost power until ~08:40 and both morning runs
  failed within minutes.
- **Automatic retries of failed sources:** a scheduled or one-time run's failed sources are tried again an hour
  later, up to 3 times (settings *Retry failed sources after (min)*, *Retries of failed sources*).

### Changed
- Hamrahan System sources (Hamrahan, Cash, Project) take the vendor's new automatic backups from
  `D:\AutoBackup\…` (share `AutoBackup$`) and keep the newest 7 / 20 / 20 there. Their freshness limit is now
  30 h, so a day without a new backup is an error, not a silent "nothing new".
- The noisy "socket aborted by peer (ECONNRESET)" message (normal when Windows closes a connection) is hidden.

### Fixed
- Deleting an archive file that was already gone crashed instead of returning "already gone"
  (an `except` clause listed a tuple inside a tuple).

## [1.7.1] — 2026-10-14

### Fixed
- **A network outage restarted big reads from zero.** The re-run of Hamrahan (11.3 GB) was 98 % read when the
  link to the 172.16.3.x site dropped for ~17 minutes; each retry started over and the source failed. Reads now
  **resume at the same byte** after reconnecting (tries for ~22 minutes; only if the file is unchanged).
  Verified by cutting a real connection in the middle of a 932 MB file: identical result.
- A dead connection is noticed after 2 minutes of silence instead of 10, and nothing waits 5 more minutes for
  "SMB credits" on a connection that is already gone.

## [1.7.0] — 2026-10-14

### Fixed
- **Backups lost to "Request requires 1 credits but only 0 credits are available"** (the first real scheduled
  runs, 2026-10-14: 11 of 17 sources failed — Edari/Finance stopped half-way, both routers, Cash, Hamrahan,
  iCan, iCan-DBs, Naghsh-Honar, HarmonyGallery). All transfers to one server shared one SMB connection; with
  several running at once it ran out of SMB credits and the library failed instead of waiting. Now every
  transfer has its own connection, a shared connection waits for credits, and a read can no longer come back
  empty and look like the end of the file.
- Unfinished `*.uploading` files and `*.partial` folders stayed in the archive after a failure (deleting them
  used the broken connection). They're now removed over a fresh connection, and any left by an interrupted run
  are removed when the service starts.
- `backuper: command not found` — `install.sh` links the command into `~/.local/bin`; the CLI finds its
  `.env` and data folder from any current folder (relative `DATA_DIR` is relative to the `.env` file).

### Added
- **Retries:** writing a zip or folder copy into the archive is tried up to 3 times (30 s, then 2 min apart);
  folder copies **resume** where they stopped. A success after a retry is noted in the report.
- **Completeness check:** every zip member and every copied file must have all its bytes, otherwise the
  attempt fails (never a valid-looking truncated backup).
- **Faster folder copies:** 4 files at a time, each on its own connection.
- **Restart from the bot:** 🔄 Restart in the main menu or `/restart` — *when backups finish* or *now*; the bot
  says when it's back. From the shell: `backuper restart [--now]`. No sudo: the service exits and systemd
  starts it again.
- **Status file lists problems:** `_backuper-status.txt` / `backuper status` now show every failed or partial
  source of the last runs with the reason, and a pending restart.

### Changed
- Folder copies skip only files the source won't let us read (locked, access denied, vanished); any other
  error fails the attempt so it can be retried.
- Finance keeps the newest **9** copies (owner's choice; ~167 GB each).

## [1.6.0] — 2026-10-13

### Added
- **Live status file** in the archive: `E:\Backuper\_backuper-status.txt` (and `data/status.txt`), refreshed
  every ~20 s during runs — every source's step with %, size done, speed and time left, the upload in progress,
  the last runs and the next scheduled ones. Works in archive-only mode. `backuper status` prints it.
- **Live progress message** in the 📊 Reports topic for scheduled and one-time runs (when Telegram works),
  edited every ~20 s and turned into the final report at the end (no duplicate report message).
- Progress everywhere now shows %, size done/total, speed and time left; archiving shows progress too; all
  sources are listed (✓ ready, ✗ failed, ⏭ nothing new) also while uploading.

### Verified
- Folder copies with paths longer than 260 characters work (the bot copies over SMB/server-side, not through
  Explorer's limit): Edari's longest path, 302 characters in the archive, copied fine.
- MikroTik Factory and DC: backups work after the router user was rebuilt.

## [1.5.0] — 2026-10-13

### Added
- **Troubleshooting logs.** Every log line is tagged with its run and source (`[run#41 Hamrahan]`), and every
  run writes its own detailed log `data/logs/runs/run-<id>.log` (DEBUG): sources and settings, which file was
  chosen and why, BACKUP options, router commands (passwords masked), API calls, zip/copy/archive/upload sizes,
  durations and speeds, every retention deletion, final result per source, tracebacks. Sent by the bot
  (📊 History → run → **📄 Log**) or printed with `backuper log [<id>] [--tail N]`. Newest 300 kept.
- Hosting panels: **Backup copy on the host** is now `no` / `newest` / `replace` — `replace` deletes the old
  backup *before* making the new one (needs room for only one backup), then keeps the new one.
  Old yes/no settings keep working (yes = `newest`).
- DirectAdmin's `.json` side file is deleted together with its backup.

### Changed
- Log format: `time level [run#N source] logger: message`; console/file stay at `LOG_LEVEL`, run files get DEBUG.

## [1.4.0] — 2026-10-13

### Added
- **Plain folder copies** — Windows folder sources can be stored as a plain dated folder in the archive
  (*Store as: copy* → `<Source>_<1405-07-21_0700>\…`) instead of a zip. On the archive's own server the
  copy is server-side (SMB copy-chunk; 85 MB in 0.1 s in a test), original file dates are kept, and a copy
  is written as `….partial` and renamed only when complete.
- **One-time scheduled runs** — "⏱ Run once at…" on a source or a group: `14:30`, a Jalali date and time
  (`1405/07/25 22:00`), a Gregorian one, or `+2h`. Listed on the ⏰ Schedule page with ✖️ Cancel; a run
  missed while the service was down still starts within an hour of a restart, otherwise it's marked missed.
- Source page shows **when it runs** (group/start/upload time, own time, manual, one-time runs) and has a
  **🗂 Group** button to move it between groups; a group's page lists its sources as buttons.
- "Send to Telegram" is asked when adding a source (hidden for plain copies).
- Documentation set: one document per code file (`docs/code/`), project overview (`docs/PROJECT.md`),
  setup & deploy guide, decision log (`docs/DECISIONS.md`), lessons learned (`docs/LESSONS.md`) and this
  changelog. `tests/test_docs.py` fails if a code file or function is undocumented.

### Changed
- MikroTik values are read with `:put [/… get …]` instead of parsing `print` output.

### Fixed
- Router identity was shown as "c": without a terminal RouterOS wraps formatted output one character per line.

### Removed
- Unused code: `util.retry_async`, `util.Throttle`, `SourceType.in_thread`, `EngineBusy` (nothing raised it
  since runs may overlap), MikroTik `parse_print`.

## [1.3.1] — 2026-10-13
### Changed
- `backuper run` works without Telegram settings (archive-only run).
- Hosting-panel test message says "kept on the host (by design)" when *Keep newest backup on the host* is on.

## [1.3.0] — 2026-10-13
### Added
- **Upload from** time per group: backups start at the group's time, uploads wait until this time
  (manual runs upload right away).
- **Keep newest backup on the host** for cPanel/DirectAdmin: this run's archive stays on the hosting
  account as an extra copy, older ones are deleted.
- **Archive-only mode**: `backuper serve` without `BOT_TOKEN`/`API_ID`/`API_HASH` still runs the schedules
  and fills the archive (no uploads, no panel).
- MikroTik setup script: custom SSH ports, NTP from the backup server (optional).

### Changed
- SMB listing uses the metadata returned with the directory listing instead of one `stat` per file
  (6,912 files: 72 s → 2 s on a slow link).

## [1.2.1] — 2026-10-13
### Added
- MikroTik test shows the router's clock, time zone and NTP state.
### Changed
- **SQL backup compression `auto`** (default): on where the edition supports it, off on Express. A real
  database went from a 5.3 GB to a 0.98 GB backup, and its run from 4.5 to 1.5 minutes.
- Windows access script: `-Path` optional for an existing share (uses the share's folder).

## [1.2.0] — 2026-10-13
### Added
- **Backup groups** (🗂 Groups): a start time + member sources; each run uploads smallest-first, one at a time.
  Sources outside groups can have their own time or be manual-only. The old global schedule is migrated into
  a group "Daily".
- **One shared upload queue**: runs may overlap, but two files are never uploaded at the same time; a source
  already running in another run is skipped (⏭); archive-only sources don't hold a group's uploads back.
- SQL "run BACKUP" sources: *Connection encryption* option (old SQL Server 2008/2012 without TLS 1.2) and
  *Keep last N counts* `mine`/`all` (also prune the application's own backups).
- Hosting panels: *Download host* (FTP certificate name, so TLS checks can stay on).
- YAML import/export of groups.
### Removed
- The global *Daily schedule* setting (replaced by groups).

## [1.1.0] — 2026-10-10
### Added
- Per-source **Send to Telegram** switch; archive-only zips are streamed straight into the archive share.
- Per-source **Archive copies to keep**.
- SQL collect: *One backup per* pattern / database / sub-folder; patterns may match sub-folder paths;
  readable labels from patterns.
- Collect sources **skip a file that was already delivered** (⏭ "no new backup since …"); if its Telegram
  upload failed it is retried automatically next run.
- Incompressible files (≥ 16 MB, sampled) are stored in the zip as-is instead of deflated.
- `backuper credential` and `backuper set` (secrets typed hidden — no passwords in chat).
- Windows access script (`docs/windows/setup-backuper-access.ps1`), SQL lock-out guide
  (`docs/mssql/03-locked-out-of-sql.md`), SQL login script for domain accounts.
### Changed
- YAML import can change a source's type.
### Fixed
- "Keep N copies" could delete the copy just written when two runs happened in the same minute (found by a test).

## [1.0.0] — 2026-10-07 (first commit 2026-10-10)
### Added
- Sources: SQL Server (collect / run `BACKUP DATABASE`), MikroTik (export + backup over SSH/SFTP), Windows
  folders (SMB), folders over SFTP/FTP/FTPS, cPanel and DirectAdmin full backups.
- AES-256 zips (streamed), archive in Jalali-dated folders with retention, Telegram uploads over MTProto
  (2 GB per part, larger files split into `.zip.001`…), smallest file first, forum topics, reports, failure DMs.
- Telegram admin panel (sources, credentials, settings, history, resend, admins) with schema-driven forms.
- Encrypted secrets, SSH host-key pinning, CLI (`serve`, `run`, `test`, `check`, `import`, `export`, `list`),
  systemd installer, SQL/MikroTik setup scripts, tests.
### Fixed during development (before the first commit)
- A typed path like `//server/share` was treated as an unknown bot command.
- The default schedule was stored as `03:00` instead of a cron line and silently ignored.
- RouterOS reports command errors as normal output; they are now detected.
- Passwords are masked in SSH error messages.
