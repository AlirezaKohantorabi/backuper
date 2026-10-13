# backuper/transport/smb.py

**Purpose:** a thin synchronous wrapper around `smbprotocol`'s `smbclient` (pure-Python SMB2/3 — no
mounting, no root, works from Linux). Paths are UNC strings (`\\server\share\dir\file`). Every call does
network I/O, so callers run it in a thread (`asyncio.to_thread`).

**Used by:** [sources](../sources/base.md) (SQL folders, Windows folders), [destinations/archive.py](../destinations/archive.md).

**Authentication:** NTLM with `DOMAIN\user` (or a local user). Sessions are cached per server+user by
`smbclient`, so many calls reuse one connection.

## Performance notes (measured on the first site)

- Listing uses the metadata that comes **with** the directory listing (`entry.smb_info`). Calling
  `entry.stat()` costs an extra round trip per file: 6,912 files took 72 s that way, 2 s now.
- Reads go up to ~55 MB/s on the LAN; the link to 172.16.3.x does ~8.7 MB/s.
- `server_copy` uses SMB copy-chunk: copying between shares *on the same server* never sends the data
  over the network (85 MB in 0.1 s in a test).

| Name | What it does |
|---|---|
| `log`, `COPY_CHUNK`, `SMBError` | Logger, 4 MB I/O size, the exceptions an SMB call can raise. |
| `_ATTR_DIRECTORY`, `_ATTR_REPARSE` | File attribute bits used while listing (skip reparse points when recursing). |
| `_ts` | Directory-listing time → Unix timestamp. |
| `RemoteFile` | A listed file: full path, path relative to the listed root, size, mtime. |
| `RemoteFile.name` | Just the file name. |
| `join` | `ntpath.join` for UNC paths. |
| `SMB` | A client bound to one username/password. |
| `SMB.__init__` | Username (`DOMAIN\user`), password, port, timeout. |
| `SMB._kw` | Arguments passed to every `smbclient` call. |
| `SMB.connect` | Registers the session for the path's server. |
| `SMB.list_files` | Lists files (optionally recursive) with include patterns and excludes. |
| `SMB.list_dirs` | Sub-folder names (archive date folders). |
| `SMB.stat` / `SMB.exists` | File info (opens a handle → current size, even while another program writes) / existence. |
| `SMB.open_read` / `SMB.opener` | Open for reading, allowing other programs to keep writing / a function that does that later. |
| `SMB.upload` | Local file → share via `.uploading` + size check + rename. |
| `SMB.open_write` | Seekable writer (zips seek back to patch headers). |
| `SMB.replace` / `SMB.rename` | Rename (replace an existing target / plain rename, also folders). |
| `SMB.server_copy` | Same-server copy (copy-chunk). |
| `SMB.copy_stream` | Copy from any readable stream into a file on the share. |
| `SMB.set_mtime` | Keep the original modification time on a copy (best effort). |
| `SMB.write_bytes` | Write a small file (archive write test). |
| `SMB.download` | Share → local file. |
| `SMB.makedirs` / `SMB.remove` / `SMB.rmtree` | Create folders / delete a file / delete a folder tree. |
| `SMB.free_space` | Free bytes on the share's volume. |
