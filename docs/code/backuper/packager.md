# backuper/packager.py

**Purpose:** turns an [`Artifact`](models.md) into one zip — AES-256 encrypted when a zip password is
set — and splits big zips for Telegram without writing the parts to disk.

**Used by:** [engine.py](engine.md) (`build_zip`, `write_zip`), [destinations/telegram.py](destinations/telegram.md)
(`FileSlice`, `part_ranges`, `part_name`).

## Key behaviours and why

- **Streaming:** entries are read in 4 MB chunks straight from their source (e.g. an SMB share) into
  the zip — a 20 GB backup never needs 20 GB of RAM or a temporary raw copy.
- **AES-256 (WinZip AE-2)** via `pyzipper`. Opens with 7-Zip/WinRAR. Windows Explorer can't open AES zips.
- **Smart compression:** files that are already compressed are stored, not deflated: by extension
  (`should_compress` in [models.py](models.md)) and, for files ≥ 16 MB, by a 2 MB sample taken after the
  header. Deflating incompressible data (e.g. SQL backups made `WITH COMPRESSION`) costs ~30 MB/s of CPU
  for nothing.
- **Zip64** is forced for entries ≥ 1 GB (sizes of live files can change while reading).
- **Locked files:** with `skip_unreadable`, files that can't be opened are skipped with a warning;
  a read error *in the middle* of a file always fails the artifact (a half-written member would corrupt the zip).
- **Integrity (1.8):** while a file is zipped, the SHA-256 of its bytes is computed from the very same
  bytes that go into the zip (`hashes=`; no second read of the source over a slow link). Right after the zip
  is stored, `verify_zip` unzips it again (streamed, nothing written) and compares every file's SHA-256;
  the zip library's own CRC and AES authentication are checked on the way. The hashes are kept next to the zip
  as `<name>.zip.sha256` (`checksum_file`) so the check can be repeated any time (`backuper verify`) or after a
  manual restore on Windows ([verify-backup.ps1](../scripts.md)). Found by its test: pyzipper raises its *own*
  `BadZipFile` class (not the standard library's) for a damaged AES member — both are caught (`_BAD_ZIP`).
- **Splitting:** Telegram (MTProto) accepts ≤ 2000 MiB per file. `part_ranges` + `FileSlice` upload
  byte ranges of the zip as `name.zip.001`, `.002`… — concatenated they are the original zip, and 7-Zip
  opens `.001` directly.

## Classes and functions

| Name | What it does |
|---|---|
| `CHUNK`, `_ZIP64_THRESHOLD`, `_AUTO_STORE_MIN`, `_SAMPLE_SKIP`, `_SAMPLE` | Read chunk size and the thresholds above. |
| `PackagingError` | Packaging failed (unreadable file, read error mid-file, nothing readable). |
| `IntegrityError` / `_BAD_ZIP` | A file that comes out of a finished zip is missing, extra, can't be unzipped, or has a different SHA-256 / both zip libraries' "bad zip" errors. |
| `verify_zip` | Unzip every member and compare its SHA-256 with the expected one; returns the number of files. |
| `checksum_file` / `parse_checksums` | `{path: sha256}` ↔ the `.sha256` text (`<sha256> *<path>` per line, the format of `sha256sum -c`). |
| `_zip_datetime` | File mtime → zip timestamp (zip can't store dates before 1980). |
| `build_zip` | Zip into a local file: writes `name.zip.part`, renames when complete (never leaves a half zip). Passes `hashes` through. |
| `write_zip` | Fills `hashes` with each member's SHA-256 if given. The actual zipping into any open seekable file object — a local file or a file on the SMB archive share (used for archive-only sources, so nothing touches the staging disk). |
| `part_ranges` | `[(offset, length), …]` covering a file in parts of at most N bytes. |
| `part_name` | `x.zip` for a single part, `x.zip.001`… for several. |
| `FileSlice` | Read-only, seekable window into a file (one part of a big zip). |
| `FileSlice.__init__` | Opens the file and positions it at the part's offset. |
| `FileSlice.readable` / `FileSlice.seekable` / `FileSlice.tell` / `FileSlice.seek` | Standard stream methods within the window. |
| `FileSlice.read` / `FileSlice.readinto` | Read, never past the part's end. |
| `FileSlice.close` | Closes the underlying file. |
