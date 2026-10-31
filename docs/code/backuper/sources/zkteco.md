# backuper/sources/zkteco.py

**Purpose:** source type `zkteco` — *ZKTeco attendance device* (first use: **Paliz Device**, the VF600 face
terminal at 192.168.10.113). Backs up its users, attendance log and face templates.

**How:** the owner's kit in [`vendor/zk-backup/`](../../../../vendor/zk-backup/README.md) does the reading. It is
used exactly as its *Integration guide for the backuper bot* says, and its invariants are kept:

- `zk_backup.py` runs as a **separate program** in its own Python environment, `.venv-zk` with **pyzk pinned
  to 0.9** (its read-only guard wraps a private pyzk method; `install.sh` creates it). The bot never imports
  the kit or speaks the device protocol, and never runs `zk_restore.py` (writing to a device stays a human
  action).
- **One output folder per device** on this server: `data/zk/source-<id>/` (mode 700 — PINs, card numbers,
  face templates). The tool refuses a different serial in the same folder.
- The result comes from the `last_run.json` the run wrote (a file older than this run is never trusted):
  `ok` → ✅, `warning` → ⚠️ with the device's warnings verbatim, anything else → ❌ with its errors verbatim.
  Exit code 3 (another run holds the folder's lock) → ⏭ skipped, not failed.
- The new snapshot is checked against its own `SHA256SUMS`, then zipped — **AES-encrypted** like every backup
  (the guide: encrypt before it leaves the host) — with `attendance_master.csv` (every punch ever seen, never
  shortened), `last_run.json` and `state.json`. The zip goes to the archive and the cloud copy like any other.
- `--keep` = *Snapshots kept on this server* (default 14); the archive keeps the source's `archive_keep`.

Restoring is described in [SETUP.md](../../../SETUP.md#restoring) and in the kit's README (dry run, one user,
then everyone, with `--confirm-serial`).

| Name | What it does |
|---|---|
| `APP_DIR` / `KIT_DIR` | The installation and the vendored kit. |
| `TOOL_TIMEOUT` | 20 minutes per run (the guide; a run takes ~1 minute). |
| `EXIT_LOCKED` | Exit code 3: skipped. |
| `zk_python` | `.venv-zk/bin/python` (or `$ZK_PYTHON`). |
| `verify_snapshot` | Checks every snapshot file against `SHA256SUMS`. |
| `ZKTeco` | The source type (category `devices`): device IP, port, Comm Key (secret), face templates, snapshots kept, name encoding, force UDP, packet timeout. |
| `ZKTeco._out_dir` | `data/zk/source-<id>`. |
| `ZKTeco._command` | The `zk_backup.py` command line (and a copy with the Comm Key masked for the log). |
| `ZKTeco._check_installed` | The kit and `.venv-zk` are there. |
| `ZKTeco.test` | `zk_backup.py --help` starts (pyzk importable), the device answers on TCP, the last run's result. No device read. |
| `ZKTeco.collect` | Runs the tool, reads `last_run.json`, maps the status, verifies the snapshot, returns one artifact; a note with serial, users, punches (new / in the master list), faces and clock drift. |
| `ZKTeco._entry` | A file as a zip entry. |
