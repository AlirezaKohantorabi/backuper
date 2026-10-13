# backuper/sources/hosting_common.py

**Purpose:** the shared "transfer leg" of the hosting-panel sources ([cPanel](cpanel.md),
[DirectAdmin](directadmin.md)): the panel creates a `backup-*.tar.gz` (or `.tar.zst`) on the hosting
account; this module finds it, waits until it's complete, downloads it over FTPS/SFTP/FTP and then
either deletes it from the host (default — nothing stays there) or keeps it as the single newest copy
on the host (*Keep newest backup on the host*).

**Uses:** [transport/remote.py](../transport/remote.md), [base.py](base.md).

## Fields shared by both panels (`TRANSFER_FIELDS`)

`transfer` (ftps/ftp/sftp), `transfer_host` (e.g. `box402.parsvds.com` — the name on the FTP
certificate, so TLS verification can stay on), `transfer_port`, `transfer_credential` (FTP login; empty =
panel login), `verify_tls`, `keep_on_host`, `timeout_minutes`.

| Name | What it does / why |
|---|---|
| `ARCHIVE_PATTERNS` | File names that count as panel backups (`backup-*.tar.gz`, `.tar.zst`, `.tar`, `.tgz`). |
| `TRANSFER_FIELDS` | See above. |
| `open_transfer` | Opens the download connection with the right host, port, login and pinned key. |
| `pin_transfer_key` | Pins the SFTP host key on first use. |
| `is_archive` | Does a file name look like a panel backup? |
| `list_archives` | Backup archives currently in a folder on the host. |
| `wait_new_archive_stable` | Waits for a *new* archive (not present before the backup started) to appear and stop growing (3 equal sizes, 20 s apart). Used by DirectAdmin, and by cPanel when its API can't report progress. |
| `download_and_delete` | Downloads the new archive. Default: deletes it from the host **even if the download failed** (nothing may stay there). `keep_on_host=True`: keeps it and deletes every *older* archive instead. |

**Lesson learned:** the FTP account must start in the account's **home folder**. A cPanel FTP account
created with the default directory (`public_html/<name>`) can't see the backups cPanel writes to the home
folder — and an existing account's folder can't be changed in cPanel (recreate it).
