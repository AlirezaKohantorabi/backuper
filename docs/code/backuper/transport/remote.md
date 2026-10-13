# backuper/transport/remote.py

**Purpose:** one small async interface (`RemoteFS`: list, download, remove) over **FTP, FTPS and SFTP**,
so the hosting-panel and remote-folder sources don't care which protocol a host offers.

**Used by:** [sources/hosting_common.py](../sources/hosting_common.md), [sources/remote_folder.py](../sources/remote_folder.md).

## Notes

- **FTPS (explicit TLS)** is the default for hosting. Certificates are verified; on shared hosting the
  certificate is usually for the server's name (`box402.parsvds.com`, `server210s.irwebspace.com`) — use
  that as host.
- `_FTPS` reuses the control connection's TLS session for data connections; many servers
  (vsftpd/pure-ftpd, common on cPanel/DirectAdmin) refuse data connections otherwise.
- Listing uses `MLSD` (sizes and dates in one go) and falls back to `NLST` + `SIZE`.
- `ftplib` is blocking → run in threads, one command at a time per connection (lock).

| Name | What it does |
|---|---|
| `log`, `PROTOCOLS`, `DEFAULT_PORTS` | Logger; `ftps`, `ftp`, `sftp`; their default ports. |
| `RemoteEntry` | A listed entry: name, path, size, is_dir, mtime. |
| `RemoteFS` | Abstract interface. |
| `RemoteFS.__aenter__` / `RemoteFS.__aexit__` | Connect / disconnect (`async with`). |
| `RemoteFS.listdir` / `RemoteFS.download` / `RemoteFS.remove` | Implemented per protocol. |
| `RemoteFS.walk_files` | All files below a folder (recursive). |
| `_FTPS` | FTP_TLS with TLS session reuse on data connections. |
| `_FTPS.ntransfercmd` | Wraps the data socket with the control connection's TLS session. |
| `FTPFS` | FTP/FTPS implementation. |
| `FTPFS.__init__` | Host, port, login, TLS on/off, verify certificates, timeout. |
| `FTPFS._connect` | Connect, log in, `PROT P` for FTPS, passive mode. |
| `FTPFS.__aenter__` / `FTPFS.__aexit__` | Connect / quit. |
| `FTPFS._listdir_sync` / `FTPFS.listdir` | Listing (MLSD or fallback). |
| `FTPFS._download_sync` / `FTPFS.download` | `RETR` into a local file (1 MB blocks). |
| `FTPFS.remove` | `DELE`. |
| `SFTPFS` | SFTP implementation over [ssh.py](ssh.md) (host key pinned). |
| `SFTPFS.__init__` | The SSH target. |
| `SFTPFS.__aenter__` / `SFTPFS.__aexit__` | Connect + start SFTP / close. |
| `SFTPFS.listdir` / `SFTPFS.download` / `SFTPFS.remove` | SFTP operations (parallel block requests for downloads). |
| `open_remote` | Factory: protocol name → `FTPFS` or `SFTPFS`. |
