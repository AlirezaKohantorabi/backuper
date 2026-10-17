"""Zip creation (optionally AES-256) and splitting for Telegram upload."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pyzipper

from backuper.models import Artifact
from backuper.util import check_complete

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024
_ZIP64_THRESHOLD = 1 << 30  # be generous: sizes of live files can change while reading
_AUTO_STORE_MIN = 16 * 1024 * 1024  # only bother sampling big files
_SAMPLE_SKIP = 1024 * 1024          # skip the (compressible) file header when sampling
_SAMPLE = 2 * 1024 * 1024


class PackagingError(RuntimeError):
    pass


# pyzipper ships its own copy of zipfile: its BadZipFile is a different class from the standard library's.
_BAD_ZIP = (zipfile.BadZipFile, pyzipper.BadZipFile)


class IntegrityError(PackagingError):
    """A file taken out of a finished zip doesn't match what went in (SHA-256), or is missing."""


def _zip_datetime(mtime: float) -> tuple[int, int, int, int, int, int]:
    t = time.localtime(mtime if mtime > 315532800 else time.time())  # zip can't store < 1980
    return t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec


def build_zip(
    artifact: Artifact,
    out_path: Path,
    *,
    password: str = "",
    level: int = 6,
    skip_unreadable: bool = False,
    progress: Callable[[int], None] | None = None,
    hashes: dict[str, str] | None = None,
) -> list[str]:
    """Write all entries of `artifact` into a zip file at `out_path`. Returns warnings."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    try:
        with open(tmp, "wb") as f:
            warnings = write_zip(artifact, f, password=password, level=level,
                                 skip_unreadable=skip_unreadable, progress=progress, hashes=hashes)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return warnings


def write_zip(
    artifact: Artifact,
    fileobj: BinaryIO,
    *,
    password: str = "",
    level: int = 6,
    skip_unreadable: bool = False,
    progress: Callable[[int], None] | None = None,
    hashes: dict[str, str] | None = None,
) -> list[str]:
    """Write all entries of `artifact` as a zip into an open, seekable binary file
    (a local file or a file on an SMB share). Returns warnings.

    Entries are streamed (never fully loaded in memory), so sources can feed
    files straight from an SMB share without a temporary copy. If `hashes` is given, it's filled with
    {member name: SHA-256 of the original bytes}, computed from the same bytes that go into the zip
    (no second read of the source) — `verify_zip` later compares them with what comes out.
    """
    warnings: list[str] = []
    written = 0
    added = 0

    if password:
        zf = pyzipper.AESZipFile(fileobj, "w", compression=pyzipper.ZIP_DEFLATED, compresslevel=level,
                                 encryption=pyzipper.WZ_AES, allowZip64=True)
        zf.setpassword(password.encode())
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        info_cls = zf.zipinfo_cls
    else:
        zf = zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True)
        info_cls = zipfile.ZipInfo

    with zf:
        for entry in artifact.entries:
            try:
                stream = entry.opener()
            except OSError as e:
                if skip_unreadable:
                    warnings.append(f"skipped {entry.arcname}: {e.__class__.__name__} {e}")
                    continue
                raise PackagingError(f"can't open {entry.arcname}: {e}") from e

            member_name = entry.arcname.replace("\\", "/")
            zi = info_cls(member_name, date_time=_zip_datetime(entry.mtime))
            digest = hashlib.sha256()
            stored = not entry.compress or level == 0
            first = b""
            try:
                if not stored and entry.size >= _AUTO_STORE_MIN:
                    # Already-compressed data (e.g. SQL backups made WITH COMPRESSION) doesn't shrink;
                    # deflating it only burns CPU (~30 MB/s). Test a sample and store it as-is instead.
                    first = stream.read(CHUNK)
                    sample = first[_SAMPLE_SKIP:_SAMPLE_SKIP + _SAMPLE] or first[:_SAMPLE]
                    stored = bool(sample) and len(zlib.compress(sample, 1)) > 0.9 * len(sample)
            except OSError as e:
                stream.close()
                raise PackagingError(f"read error in {entry.arcname}: {e}") from e
            zi.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            if not stored:
                zi._compresslevel = level  # noqa: SLF001 - honoured by zipfile and pyzipper
            zi.file_size = entry.size
            zi.external_attr = 0o644 << 16
            try:
                with stream, zf.open(zi, "w", force_zip64=entry.size >= _ZIP64_THRESHOLD) as dst:
                    member = len(first)
                    if first:
                        digest.update(first)
                        dst.write(first)
                        written += len(first)
                    while True:
                        buf = stream.read(CHUNK)
                        if not buf:
                            break
                        digest.update(buf)
                        dst.write(buf)
                        written += len(buf)
                        member += len(buf)
                        if progress:
                            progress(written)
                    check_complete(stream, member, entry.arcname)
            except OSError as e:
                # A half-written member corrupts the zip: never skip, fail the artifact.
                raise PackagingError(f"read error in {entry.arcname}: {e}") from e
            if hashes is not None:
                hashes[member_name] = digest.hexdigest()
            added += 1
    if added == 0 and artifact.entries:
        raise PackagingError("no readable files - nothing was zipped")
    return warnings


def verify_zip(fileobj: BinaryIO, expected: dict[str, str], *, password: str = "",
               progress: Callable[[int], None] | None = None) -> int:
    """Unzip every member of a finished zip (in memory, streamed) and compare its SHA-256 with the hash
    taken while zipping. Raises IntegrityError on a missing, extra or different file (the zip library's own
    CRC / AES checks raise too). Returns the number of files checked."""
    try:
        zf = pyzipper.AESZipFile(fileobj)
    except (*_BAD_ZIP, OSError, ValueError) as e:
        raise IntegrityError(f"not a readable zip: {e}") from e
    with zf:
        if password:
            zf.setpassword(password.encode())
        members = [i for i in zf.infolist() if not i.is_dir()]
        names = {i.filename for i in members}
        missing, extra = sorted(set(expected) - names), sorted(names - set(expected))
        if missing or extra:
            raise IntegrityError(f"zip content differs: missing {missing[:5]}, unexpected {extra[:5]}")
        done = 0
        for info in members:
            digest = hashlib.sha256()
            try:
                with zf.open(info) as m:
                    while buf := m.read(CHUNK):
                        digest.update(buf)
                        done += len(buf)
                        if progress:
                            progress(done)
            except (*_BAD_ZIP, RuntimeError, ValueError) as e:  # CRC / AES authentication / password
                raise IntegrityError(f"{info.filename}: can't be unzipped correctly: {e}") from e
            if digest.hexdigest() != expected[info.filename]:
                raise IntegrityError(f"{info.filename}: SHA-256 after unzipping differs from the original")
    return len(members)


def checksum_file(hashes: dict[str, str]) -> str:
    """The `.sha256` file stored next to each zip: one `<sha256> *<path>` line per file (the format of
    `sha256sum -c`; `verify-backup.ps1` checks extracted files against it on Windows)."""
    return "".join(f"{h} *{name}\n" for name, h in sorted(hashes.items()))


def parse_checksums(text: str) -> dict[str, str]:
    """Read a `.sha256` file back into {path: sha256}."""
    out = {}
    for line in text.lstrip("\ufeff").splitlines():
        h, sep, name = line.partition(" *")
        if sep and len(h) == 64:
            out[name] = h.lower()
    return out


def part_ranges(size: int, part_size: int) -> list[tuple[int, int]]:
    """[(offset, length), ...] covering a file of `size` bytes."""
    if size <= part_size:
        return [(0, size)]
    return [(off, min(part_size, size - off)) for off in range(0, size, part_size)]


def part_name(file_name: str, index: int, total: int) -> str:
    return file_name if total == 1 else f"{file_name}.{index:03d}"


class FileSlice(io.RawIOBase):
    """A read-only, seekable window into a file — lets us upload part N of a big
    zip without writing the parts to disk first. Concatenating the parts gives
    back the original zip (7-Zip opens name.zip.001 directly)."""

    def __init__(self, path: Path, offset: int, length: int, name: str):
        super().__init__()
        self._f = open(path, "rb")  # noqa: SIM115 - closed in close()
        self._start = offset
        self._len = length
        self._pos = 0
        self.name = name
        self._f.seek(offset)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            new = pos
        elif whence == io.SEEK_CUR:
            new = self._pos + pos
        else:
            new = self._len + pos
        self._pos = max(0, min(new, self._len))
        self._f.seek(self._start + self._pos)
        return self._pos

    def read(self, n: int = -1) -> bytes:
        remaining = self._len - self._pos
        if remaining <= 0:
            return b""
        if n is None or n < 0 or n > remaining:
            n = remaining
        data = self._f.read(n)
        self._pos += len(data)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._f.close()
        finally:
            super().close()
