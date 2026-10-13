# backuper/sources/smb_folder.py

**Purpose:** source type **"Windows folder (SMB share)"** — a whole folder tree, stored either as
**one AES-256 zip** (`format: zip`, can also go to Telegram) or as a **plain dated folder** in the archive
(`format: copy` → `<Source>_<1405-07-21_0700>\…`, archive only, original file dates kept; used for Edari
and Finance so files can be browsed and restored directly).

**Uses:** [transport/smb.py](../transport/smb.md) (listing with the directory metadata — one round trip
per folder, not per file), [base.py](base.md). The copy itself is done by
[destinations/archive.py](../destinations/archive.md) `copy_tree` — server-side when the folder is on the
archive's server.

## Fields

`format` (zip/copy), `path` (UNC; admin shares like `\\srv\D$\x` need an admin account), `credential`,
`exclude` (Office lock files, temp files, Thumbs.db, recycle bin… by default), `skip_locked` (+ common).

| Name | What it does |
|---|---|
| `DEFAULT_EXCLUDE` | Default exclude patterns. |
| `SMBFolder` | The source type (`type_id = "smb_folder"`, category files). |
| `SMBFolder.describe` | The path. |
| `SMBFolder._list` | Recursive listing with excludes. |
| `SMBFolder.test` | File count and total size. |
| `SMBFolder.collect` | One artifact with every file (streamed from the share); `format` and `skip_unreadable` passed to the engine; `source_path` set so a same-server copy can be done server-side. |

**Note:** very large trees (e.g. iCan's Upload folder: >13,700 folders) take minutes just to list over a
slow link — each folder costs a network round trip.
