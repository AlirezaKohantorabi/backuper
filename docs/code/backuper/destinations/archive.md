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
| `Archive._smb` / `Archive._root_unc` | SMB client with the archive credential; normalised root path. |
| `Archive._store_sync` / `Archive.store_file` | Copy a finished local zip in (see table above). |
| `Archive._write_direct_sync` / `Archive.write_direct` | Create a file in the archive by streaming into it. |
| `Archive._copy_tree_sync` / `Archive.copy_tree` | Plain folder copy (see table above). |
| `Archive._delete_sync` / `Archive.delete` | Delete a zip or a copied folder (no error if already gone). |
| `Archive._fetch_sync` / `Archive.fetch` | Download a zip back (📤 Resend). |
| `Archive._list_date_folders_sync` | Date folders, oldest first. |
| `Archive._prune_sync` / `Archive.prune` | Date-folder retention. |
| `Archive._test_sync` / `Archive.test` | Write+delete a probe file, report free space and folder count. |

The `_…_sync` functions do the blocking SMB/disk work; the public `async` ones run them in a thread.
