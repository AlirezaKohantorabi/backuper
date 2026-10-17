# backuper/models.py

**Purpose:** the plain data shapes (dataclasses) that the rest of the code passes around, plus the
run-status constants. No I/O, no logic beyond tiny helpers — safe to import from anywhere.

**Used by:** practically every module — [db](db.md) builds `Credential`/`Group`/`OneOff`/`SourceRecord`
from rows, [sources](sources/base.md) return `Artifact`s made of `Entry`s, the [engine](engine.md)
turns them into `PackagedFile`s and `ItemResult`s, [telegram](destinations/telegram.md) uses
`CATEGORY_TITLES`.

## Constants

| Name | Meaning |
|---|---|
| `OK`, `WARNING`, `PARTIAL`, `FAILED`, `SKIPPED` | Status of one source in one run. PARTIAL = stored somewhere but not everywhere (e.g. archive OK, Telegram failed). SKIPPED = nothing new / already running. |
| `STATUS_ICON` | Emoji per status for reports and menus (✅ ⚠️ 🟠 ❌ ⏭). |
| `CATEGORY_TITLES` | Default topic name per source category (databases, network, websites, files, system, reports). |
| `_STORED_EXT` | File extensions that are already compressed; zipped with STORE instead of DEFLATE. |

## Classes and functions

| Name | What it is / does |
|---|---|
| `Credential` | A stored login: `name`, `username`, decrypted `password`, `note`. |
| `Group` | A backup group: `name`, `schedule` (cron lines joined by `;`), `enabled`, `upload_at` ("HH:MM" — scheduled runs don't upload before this). |
| `OneOff` | A one-time scheduled run: `label`, `source_ids`, `run_at` (aware datetime), `status` (pending/running/done/missed/cancelled/failed), `run_id` once it ran, `retry` (0 = made by a person, N = automatic retry no. N of failed sources). |
| `SourceRecord` | One configured source: `id`, `name`, `type` (registry key), `enabled`, `config` (field values, secrets decrypted), `state` (plugin memory such as pinned SSH keys, last delivered file). |
| `Entry` | One file that goes into a zip / copy: `arcname` (path inside), `size`, `mtime`, `opener()` returning a readable stream, `compress` flag, and `source_path` (UNC of the original — lets the archive do a server-side copy). |
| `Entry.from_path` | Build an `Entry` for a local file (used after downloads: MikroTik, cPanel, SFTP). |
| `Artifact` | Everything one zip (or one plain copy) is made of: `label` (item name, e.g. database), `entries`, `meta` (e.g. `group`, `format`, `skip_unreadable`, `delivered`), `warnings`, `cleanup` (temp files to delete after packaging). |
| `Artifact.total_size` | Sum of the entries' sizes (progress + disk checks). |
| `PackagedFile` | The result of packaging one artifact: file/folder name, size, local path (if staged), `archive_rel` ("<date>/<name>" in the archive), upload state, `send_telegram`, `verified` (number of files unzipped again and found identical), `cloud` (uploaded to the cloud copy and MD5-checked by this run). Stored as JSON in `run_items.files` so "Resend" can find it later. |
| `ItemResult` | Outcome of one source in one run: status, message, files, warnings, timings. |
| `ItemResult.total_size` | Sum of its files' sizes (reports, history). |
| `should_compress` | `False` for already-compressed formats by extension (zip, gz, jpg, mp4, docx…). |

**Why dataclasses and not ORM models?** The data is small and mostly read whole; plain dataclasses keep
modules decoupled from SQLite and make tests trivial to set up.
