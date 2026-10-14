# backuper/transport/smb.py

**Purpose:** a thin synchronous wrapper around `smbprotocol`'s `smbclient` (pure-Python SMB2/3 — no
mounting, no root, works from Linux). Paths are UNC strings (`\\server\share\dir\file`). Every call does
network I/O, so callers run it in a thread (`asyncio.to_thread`).

**Used by:** [sources](../sources/base.md) (SQL folders, Windows folders), [destinations/archive.py](../destinations/archive.md).

**Authentication:** NTLM with `DOMAIN\user` (or a local user).

**One connection per `SMB` object (since 1.7.0).** Each `SMB` object keeps its own connection per server
(its own `smbclient` connection cache) and is closed with `close()` / `with SMB(...) as smb:`. Before
1.7.0 every transfer to the same server shared one process-wide connection; when several sources wrote into
the archive at once that connection ran out of SMB **credits** (the server's flow-control tokens) and
`smbprotocol` failed instantly with *"Request requires 1 credits but only 0 credits are available"* — the
first real scheduled runs (2026-10-14) lost 11 of 17 backups that way. Reproduced with 6 parallel writers
(5 failed in 3.5 s); with separate connections 10 writers × 384 MB had no errors (~100 MB/s).

**Credit guard.** `_install_credit_guard()` (runs on import) also patches `smbprotocol` so that, if one
connection *is* shared by several threads:
- a request waits (up to `CREDIT_WAIT`) until the credits for the whole request are there, instead of
  failing — and a compound request can no longer fail half-way and leak the credits of its first part;
- a read/write is never sent with a 0-byte size when credits are at 0 (a 0-byte read looks like
  end-of-file: a silently truncated copy).

`copy_stream` and `download` also check that every byte of the source arrived
([`check_complete`](../util.md)).

## Performance notes (measured on the first site)

- Listing uses the metadata that comes **with** the directory listing (`entry.smb_info`). Calling
  `entry.stat()` costs an extra round trip per file: 6,912 files took 72 s that way, 2 s now.
- Reads go up to ~55 MB/s on the LAN; the link to 172.16.3.x does ~8.7 MB/s.
- `server_copy` uses SMB copy-chunk: copying between shares *on the same server* never sends the data
  over the network (85 MB in 0.1 s in a test).

| Name | What it does |
|---|---|
| `log`, `COPY_CHUNK`, `SMBError` | Logger, 4 MB I/O size, the exceptions an SMB call can raise. |
| `CREDIT_WAIT` | Seconds a request may wait for SMB credits (300) before failing. |
| `_credits` | Credits currently free on a connection. |
| `_install_credit_guard` | The `smbprotocol` patch described above (idempotent; checked by `tests/test_smb_guard.py`). |
| `_ATTR_DIRECTORY`, `_ATTR_REPARSE` | File attribute bits used while listing (skip reparse points when recursing). |
| `_ts` | Directory-listing time → Unix timestamp. |
| `RemoteFile` | A listed file: full path, path relative to the listed root, size, mtime. |
| `RemoteFile.name` | Just the file name. |
| `join` | `ntpath.join` for UNC paths. |
| `SMB` | A client bound to one username/password. |
| `SMB.__init__` | Username (`DOMAIN\user`), password, port, timeout; an empty connection cache of its own. |
| `SMB._base_kw` | Login, port, timeout and this object's connection cache. |
| `SMB._kw` | Arguments for an `smbclient` call on a path — connects first. |
| `SMB.connect` | Registers the session for the path's server (under a lock: two threads registering the first session at once could tear it down). |
| `SMB.close` / `SMB.__enter__` / `SMB.__exit__` | Close this object's connections (safe any time — the next call reconnects) / use as `with`. |
| `SMB.list_files` | Lists files (optionally recursive) with include patterns and excludes. |
| `SMB.list_entries` | Name, is-folder and modified time of everything directly in a folder (leftover clean-up). |
| `SMB.list_dirs` | Sub-folder names (archive date folders). |
| `SMB.is_dir` | Is the path a folder? |
| `SMB.stat` / `SMB.exists` | File info (opens a handle → current size, even while another program writes) / existence. |
| `SMB.open_read` / `SMB.opener` | Open for reading, allowing other programs to keep writing / a function that does that later. |
| `SMB.upload` | Local file → share via `.uploading` + size check + rename. |
| `SMB.open_write` | Seekable writer (zips seek back to patch headers). |
| `SMB.replace` / `SMB.rename` | Rename (replace an existing target / plain rename, also folders). |
| `SMB.server_copy` | Same-server copy (copy-chunk). |
| `SMB.copy_stream` | Copy from any readable stream into a file on the share; raises `IncompleteCopy` if the source ended early. |
| `SMB.set_mtime` | Keep the original modification time on a copy (best effort). |
| `SMB.write_bytes` | Write a small file (archive write test). |
| `SMB.download` | Share → local file (completeness checked). |
| `SMB.makedirs` / `SMB.remove` / `SMB.rmtree` | Create folders / delete a file / delete a folder tree. |
| `SMB.free_space` | Free bytes on the share's volume. |
