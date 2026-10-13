# Changelog

Every version, what was added, changed, fixed and removed — with the date. Versions follow
[semantic versioning](https://semver.org): MAJOR for incompatible changes, MINOR for new features,
PATCH for fixes. Git tags `vX.Y.Z` point to the commit of each version.

The *why* behind the bigger changes is in [docs/DECISIONS.md](docs/DECISIONS.md); what we learned on
the way (mistakes included) is in [docs/LESSONS.md](docs/LESSONS.md).

---

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
