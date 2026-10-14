# backuper/engine.py

**Purpose:** the heart of the program — one **run** of a set of sources, from collecting the data to
the report. Everything else either triggers runs ([scheduler](scheduler.md), [panel](bot/panel.md),
[cli](cli.md)) or is used by them ([sources](sources/base.md), [packager](packager.md),
[archive](destinations/archive.md), [telegram](destinations/telegram.md)).

## What a run does

```
run(source_ids, trigger, upload_from)
 │
 ├─ skip sources that are already running in another run (status ⏭)
 ├─ Phase 1  for each source in parallel (limit: setting "Parallel sources"):
 │     plugin.collect()  → Artifacts
 │     for each Artifact:
 │        format "copy"     → archive.copy_tree()            (plain folder, archive only)
 │        Telegram off/none → write_zip() straight into the archive (no staging copy)
 │        otherwise         → build_zip() in staging → archive.store_file()
 ├─ (wait for "upload from" time, if the group has one)
 ├─ Phase 2  acquire the global upload lock (one upload at a time across ALL runs)
 │     upload this run's zips smallest first; big ones in ≤ 1.95 GB parts
 │     (archive-only sources keep working meanwhile — they don't hold uploads back)
 ├─ Phase 3  per source: status, source retention (only for safely stored data),
 │     per-source archive retention; then archive date-folder retention
 └─ save history, post the report to the Reports topic, DM admins on problems
```

**Statuses:** ✅ OK · ⚠️ OK with warnings · 🟠 PARTIAL (stored somewhere, not everywhere) ·
❌ FAILED (stored nowhere) · ⏭ SKIPPED (nothing new / already running).

**Safety rules built in:**
- source retention ("keep last N") runs only for artifacts that reached the archive (or Telegram if
  there's no archive);
- a zip that reached neither the archive nor Telegram is moved to `staging/unsent/` instead of deleted;
- "keep N copies" never deletes a file that also belongs to a kept copy (same-minute re-runs overwrite
  a file with the same name — this was a real bug caught by a test);
- without Telegram configured (archive-only mode) every zip goes straight to the archive.

## Seeing a run in progress

- **Manual runs** (▶️ in the bot): a progress message in the admin's chat, updated every few seconds.
- **Scheduled and one-time runs** (when Telegram works): a live message in the 📊 Reports topic, updated
  every ~20 s, which turns into the final report at the end.
- **Always** (also archive-only mode): the [status file](status.md) `E:\Backuper\_backuper-status.txt`
  and `backuper status`.

## Logging

Each run sets the run id (and each source task its source name) in [logctx](logctx.md), so every line is
tagged `[run#N Source]`, and opens `data/logs/runs/run-<id>.log` (DEBUG detail). The engine logs: the run's
sources and settings, collection result per source, zip/copy/archive sizes, durations and speeds, the upload
order, each upload's parts and speed, retention deletions, archive pruning, each source's final result and
warnings, and full tracebacks for unexpected errors.

## Classes and functions

| Name | What it does / why |
|---|---|
| `ProgressCallback` | Type of the function that receives progress text (the bot edits a message with it). |
| `_speed` | "25.8 MB/s" for log lines. |
| `MIN_FREE_BYTES` | Refuse to zip into staging with less than 1 GB free. |
| `SourceProgress` | Live state of one source in a run (phase, bytes done/total, when the phase started). |
| `SourceProgress.begin` | Start a new step: resets the byte counter and the clock used for speed and time left. |
| `SourceProgress.line` | "Edari: copying → archive 41% · 8.7 GB / 21.2 GB · 52.0 MB/s · ~4m 10s left" (✓ ready, ✗ failed, ⏭ nothing new). |
| `DONE_ICONS` | Markers for finished steps. |
| `progress_text` | "41% · size done / total · speed · time left" (or the elapsed time if the size is unknown). |
| `RunProgress` | Live state of a run (phase, sources, current upload with its start time). |
| `RunProgress.render` | The run's live status: HTML for Telegram (progress message, `/status`), plain text for the [status file](status.md). Lists every source and the upload in progress. |
| `_Pending` | Keeps the plugin instance and its (artifact, packaged file) pairs until phase 3 needs them. |
| `Engine` | One per process; holds the upload lock, the active-source map and the live runs. |
| `Engine.__init__` | Wires config, store, sender (or `None` = archive-only) and the archive. |
| `Engine.busy` | True while any run is active. |
| `Engine.render_status` | Progress of every active run (for `/status`). |
| `Engine.startup_cleanup` | Marks crashed runs "interrupted", deletes leftover staging folders. |
| `Engine._prune_unsent` | Deletes `staging/unsent` zips older than the archive retention. |
| `Engine.run` | The run described above. `upload_from="HH:MM"` (groups only) delays phase 2 until that time; manual and one-time runs upload right away. Returns the run id. |
| `Engine._describe` | One-line, secret-free summary of a source for the run log. |
| `Engine._uploads` | Will this source produce Telegram uploads? (Telegram configured, "Send to Telegram" on, not a plain copy.) Only those are awaited before phase 2. |
| `Engine._next_time` | Next occurrence of "HH:MM" (today or tomorrow). |
| `Engine._wait_until` | Sleeps until the upload time, updating the progress text. |
| `Engine._process_source` | Phase 1 for one source: runs the plugin, packages each artifact, deletes temp downloads, catches every error so one source never breaks the run; turns "nothing new" notes into ⏭. |
| `Engine._package` | One artifact → plain copy / zip streamed into the archive / zip in staging + copy to archive. Builds the file name `<Source>[_<item>]_<1405-07-21_0800>.zip`. |
| `Engine._upload_all` | Phase 2: this run's Telegram files sorted by size, one after another. |
| `Engine._upload_one` | Upload one file (in parts if needed) with captions; records errors instead of raising. |
| `Engine._caption` | Telegram caption: source, item, Jalali time, size (and raw size), part i/n, 🔐, hashtags. |
| `Engine._finish_source` | Phase 3 for one source: final status, per-source archive retention, plugin retention (`after_success`) for safely stored artifacts; sets `delivered` so collect sources don't resend unchanged files. |
| `Engine._prune_source_archive` | "Archive copies to keep": remembers which archive paths each run produced and deletes the oldest beyond N (never one that is also part of a kept copy). |
| `Engine._keep_unsent` | Moves zips that went nowhere to `staging/unsent`. |
| `Engine._report` | Builds the run's status and report text (HTML for Telegram, ≤ 4000 chars). |
| `Engine._publish_report` | Posts the report to the Reports topic (unless the live progress message already turned into the report) and DMs admins on warnings/failures. |
| `Engine._file_dict` | `PackagedFile` → JSON-able dict for history ("Resend" needs it). |
| `Engine._ticker` | Pushes progress text while a run is going (every ~6 s for the admin's own progress message, every ~20 s for the live message in the group), only when it changed. |
| `ARCHIVE_TRIES` / `RETRY_WAITS` / `Engine._with_retries` | Writing a zip/copy into the archive is tried up to 3 times (30 s, then 120 s apart); the source's connections are reset in between and folder copies resume. A success after a retry is noted in the report; a copy that keeps failing has its `.partial` folder removed. |
| `Engine.clean_archive_leftovers` | At service start: removes unfinished `.uploading`/`.partial` items of interrupted runs (see [archive](destinations/archive.md)). |
| `Engine.resend_item` | "📤 Resend" from history: fetches the zip from the archive (or `unsent`) and uploads it again, through the same upload lock. |
