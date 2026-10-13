# backuper/sources/remote_folder.py

**Purpose:** source type **"Folder over SFTP / FTP / FTPS"** — downloads a remote folder (Linux server,
NAS, hosting account) into staging and zips it. Optional *Delete remote files after* turns it into a
"move" (for servers that drop their own dumps somewhere).

**Uses:** [transport/remote.py](../transport/remote.md), [base.py](base.md).

| Name | What it does |
|---|---|
| `RemoteFolder` | The source type (`type_id = "remote_folder"`, category files). |
| `RemoteFolder.describe` | `protocol://host/path`. |
| `RemoteFolder._fs` | Opens FTP/FTPS/SFTP with the stored login (SFTP host key pinned). |
| `RemoteFolder._pin` | Pins the SFTP host key on first connection. |
| `RemoteFolder._filter` | Applies exclude patterns. |
| `RemoteFolder.test` | File count and size. |
| `RemoteFolder.collect` | Downloads everything, returns one artifact (temp files deleted after zipping). |
| `RemoteFolder.after_success` | Deletes the downloaded remote files if *Delete remote files after* is on. |
