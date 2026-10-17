# backuper/destinations/cloud.py

**Purpose:** the off-site copy that replaces the Telegram uploads (Telegram was dropped when my.telegram.org
wouldn't issue an API id). The newest `cloud_keep_days` date folders of the archive are **mirrored** to Google
Drive (or to any folder rclone can write, e.g. a USB disk mounted on the bot server).

**How a sync works** (after every run, under the same "one upload at a time" lock the Telegram uploads used):

1. *Wanted*: every `*.zip` directly in the newest N date folders of the archive — so plain folder copies
   (Edari/Finance) are never sent — except zips of sources whose **Send to cloud** is off (by file-name prefix).
2. *Present*: one listing of the cloud folder with sizes and MD5s.
3. Upload what's missing or has a different size, **smallest first, one at a time**, with rclone streaming it
   from the archive share to the cloud (`copyto`; resumable 64 MB chunks, retries).
4. **Check each upload:** MD5 of the file in the archive (read over the LAN) must equal the MD5 Google Drive
   computed. A difference deletes the cloud copy; the source becomes 🟠 partial.
5. Remove cloud date folders older than the newest N (Jalali-named folders only).

Because it compares with what's already there, nothing is tracked separately: a failed upload, a day without
internet or a manual run is simply caught up by the next sync.

**rclone** (installed by `install.sh` into `.venv/bin`) is configured only through environment variables built
from the encrypted settings at each call — `cloud:` (Drive with scope `drive.file`: rclone can see and delete
only the files it created; deleted files skip the trash so they don't keep using the quota) and `archive:` (the
SMB share, with the archive credential's password in rclone's obscured form, passed on stdin). No rclone config
file with tokens is ever written.

**Google sign-in:** `backuper cloud login` prints the command to run on a PC with a browser
(`rclone authorize "drive" "…"`) and stores the token it prints in a credential (encrypted). rclone warned in
2026 that its *shared* Google client ID is being retired; the login accepts your own OAuth client ID/secret.

**Used by:** [engine.py](../engine.md) (phase 4 of a run), [cli.py](../cli.md) (`backuper cloud`).

| Name | What it does |
|---|---|
| `DRIVE_SCOPE` / `DRIVE_CHUNK` / `COMMAND_TIMEOUT` | `drive.file`; 64 MB upload chunks; 6 h per upload at most. |
| `CloudError` | A cloud step failed (rclone missing, not signed in, upload/verify failed). |
| `rclone_binary` | `$RCLONE_BINARY`, `.venv/bin/rclone`, or `rclone` on the PATH. |
| `drive_authorize_command` | The `rclone authorize "drive" "<base64 options>"` command for the PC with the browser (optionally with your own client ID). |
| `SyncResult` / `SyncResult.summary` | What a sync did (uploaded, failed, removed, already there, time) / the "☁ cloud: …" report line. |
| `Cloud` / `Cloud.__init__` | The cloud copy, built on the store and the [archive](archive.md). |
| `Cloud.kind` / `Cloud.enabled` / `Cloud.folder` / `Cloud.keep_days` | Settings `cloud_type` (off/drive/local), on only with an archive, `cloud_folder`, `cloud_keep_days`. |
| `Cloud._env` | The rclone environment (`cloud:` + `archive:` remotes). |
| `Cloud._obscure` | rclone's obscured form of the archive password (via stdin). |
| `Cloud._cloud_path` / `Cloud._archive_path` | `cloud:<folder>/<rel>` / `archive:<share>/<rel>` (or a local path). |
| `Cloud._rclone` | Runs rclone; parses its JSON stats for progress; turns errors into `CloudError`. |
| `Cloud._list_cloud` / `Cloud._list_cloud_file` | Everything in the cloud folder with size + MD5 / one file. |
| `Cloud._archive_md5` | MD5 of a zip in the archive. |
| `Cloud.wanted` | The zips that should be in the cloud. |
| `Cloud.sync` | Steps 1–5 above. |
| `Cloud._prune` | Step 5. |
| `Cloud.test` | Upload, check and delete a small file; report used/free space. |
