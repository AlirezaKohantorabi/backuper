# backuper/destinations/archive.py

**Purpose:** the long-term archive — `<archive_path>\<Jalali date>\<files>` — on a Windows share
(`\\192.168.10.35\Backuper`, written over SMB) or a local folder on the bot server. Reads its location
and credential from ⚙️ Settings every time, so changes apply without a restart.

**Uses:** [transport/smb.py](../transport/smb.md), [jalali.py](../jalali.md), [db.py](../db.md),
[fields.py](../fields.md) (`unc_server`), [models.py](../models.md) (`Artifact`).
**Used by:** [engine.py](../engine.md) (store, write, copy, prune, delete, fetch), [bot/panel.py](../bot/panel.md)
(🧪 Test archive), [cli.py](../cli.md) (`check`).

## Ways something gets into the archive

| Method | When | How |
|---|---|---|
| `store_file` | Zips that also go to Telegram | zip built in staging, then copied (`.uploading` → rename, size verified). |
| `write_direct` | Archive-only zips (or no Telegram configured) | zip streamed straight into the share — nothing on the staging disk. |
| `copy_tree` | "Store as: copy" folders (Edari/Finance) | plain files into `<date>\<Source>_<stamp>\`; when the source is on the same server as the archive, **server-side copy** (SMB copy-chunk: the data never crosses the network); original file dates kept; written as `….partial` and renamed when complete. |

## Retention

- `prune()` keeps the newest *N* date folders (⚙️ Archive days to keep) and deletes older ones — **only
  folders named like a Jalali date** are ever touched.
- Per-source "Archive copies to keep" is done by the engine with `delete()` (files or copied folders).

## Classes and functions

| Name | What it does |
|---|---|
| `log`, `T` | Logger; generic type for `write_direct`'s return value. |
| `ArchiveError` | Archive misconfigured (e.g. its credential was deleted). |
| `Archive` | The archive. |
| `Archive.__init__` | Keeps the store. |
| `Archive.root` / `Archive.enabled` / `Archive.is_smb` | Current location; empty = Telegram only; share vs local folder. |
| `Archive._smb` / `Archive._root_unc` | A new SMB client (own connection, used with `with`) with the archive credential; normalised root path. |
| `Archive._discard` | After a failure: removes the unfinished `.uploading` file / `.partial` folder over a *fresh* connection (the failed one may be dead); 3 tries, never raises. |
| `_is_unreadable` | Is an error about the *source file* (access denied, locked, vanished, changed while read)? Only those are skipped in folder copies; connection/archive errors fail the copy so it's retried. |
| `COPY_WORKERS` / `Archive._copy_files_parallel` | Folder copies use 4 worker threads, each with its own connection (copying many small files is mostly round trips). The first real error stops all workers. |
| `Archive._verify_sync` / `Archive.verify` | Unzip a zip in the archive (streamed through a resumable reader, nothing written) and compare every file's SHA-256, or with `None` just check the zip's own AES/CRC ([packager.verify_zip](../packager.md)). |
| `Archive._list_zips_sync` / `Archive.list_zips` / `Archive.list_date_folders` | Zips in a date folder / the date folders (for `backuper verify`). |
| `Archive._ping_sync` / `Archive.ping` | Is the archive reachable right now? (Lists the root; 2-minute limit.) Used to wait for it at the start of a run. |
| `Archive.discard_copy` | Removes `<name>.partial` when the engine gives up on a copy. |
| `LEFTOVER_SUFFIXES` / `LEFTOVER_MIN_AGE` / `Archive._clean_leftovers_sync` / `Archive.clean_leftovers` | At service start: delete `*.uploading` files and `*.partial` folders in date folders that are older than 15 minutes (left by a run that was interrupted). |
| `Archive._store_sync` / `Archive.store_file` | Copy a finished local zip in (see table above). |
| `Archive._write_direct_sync` / `Archive.write_direct` | Create a file in the archive by streaming into it. |
| `Archive._copy_tree_sync` / `Archive.copy_tree` | Plain folder copy (see table above). |
| `Archive._delete_sync` / `Archive.delete` | Delete a zip or a copied folder (no error if already gone). |
| `Archive._fetch_sync` / `Archive.fetch` | Download a zip back (📤 Resend). |
| `Archive.write_status` | Writes the live status file (`_backuper-status.txt`) in the archive root (UTF-8 with BOM). |
| `Archive._list_date_folders_sync` | Date folders, oldest first. |
| `Archive._prune_sync` / `Archive.prune` | Date-folder retention. |
| `Archive._test_sync` / `Archive.test` | Write+delete a probe file, report free space and folder count. |

The `_…_sync` functions do the blocking SMB/disk work; the public `async` ones run them in a thread.

## Failures and retries (since 1.7.0)

- Every operation uses its own SMB connection and closes it (see [transport/smb.py](../transport/smb.md)).
- A zip that fails while being written is removed (`.uploading`), a failed folder copy keeps its
  `.partial` folder so the engine's retry **resumes** it (files already there with the same size and date
  are not copied again). When the engine gives up, the `.partial` folder is deleted.
- Files the source won't let us read (locked, access denied) are skipped and listed as warnings; any
  other error fails the attempt.

