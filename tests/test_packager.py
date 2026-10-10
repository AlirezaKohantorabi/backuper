import io
import os

import pyzipper
import pytest

from backuper.models import Artifact, Entry
from backuper.packager import FileSlice, PackagingError, build_zip, part_name, part_ranges


def _entry(name: str, data: bytes) -> Entry:
    return Entry(arcname=name, size=len(data), mtime=1_700_000_000, opener=lambda: io.BytesIO(data))


def test_aes_zip_roundtrip(tmp_path):
    data = os.urandom(300_000) + b"\0" * 2_000_000
    art = Artifact(label="", entries=[_entry("db.bak", data), _entry("sub/info.txt", b"hello")])
    out = tmp_path / "x.zip"
    assert build_zip(art, out, password="s3cret", level=6) == []
    with pyzipper.AESZipFile(out) as z:
        assert all(i.flag_bits & 1 for i in z.infolist())  # every member encrypted
        z.setpassword(b"s3cret")
        assert z.read("db.bak") == data
        assert z.read("sub/info.txt") == b"hello"
    with pyzipper.AESZipFile(out) as z, pytest.raises(RuntimeError):
        z.setpassword(b"wrong")
        z.read("db.bak")


def test_plain_zip_and_skip_unreadable(tmp_path):
    def boom():
        raise PermissionError("locked")

    art = Artifact(label="", entries=[_entry("a.txt", b"a"),
                                      Entry(arcname="locked.xlsx", size=1, mtime=0, opener=boom)])
    out = tmp_path / "p.zip"
    warnings = build_zip(art, out, skip_unreadable=True)
    assert len(warnings) == 1 and "locked.xlsx" in warnings[0]
    with pyzipper.ZipFile(out) as z:
        assert z.namelist() == ["a.txt"]
    with pytest.raises(PackagingError):
        build_zip(art, tmp_path / "q.zip", skip_unreadable=False)
    assert not (tmp_path / "q.zip").exists() and not (tmp_path / "q.zip.part").exists()


def test_split_parts_concatenate_to_original(tmp_path):
    f = tmp_path / "big.zip"
    payload = os.urandom(1_000_003)
    f.write_bytes(payload)
    ranges = part_ranges(len(payload), 300_000)
    assert len(ranges) == 4
    joined = b""
    for i, (off, ln) in enumerate(ranges, 1):
        with FileSlice(f, off, ln, part_name("big.zip", i, len(ranges))) as s:
            assert s.name == f"big.zip.{i:03d}"
            chunk = s.read()
            assert len(chunk) == ln
            s.seek(0)
            assert s.read(10) == chunk[:10]
            joined += chunk
    assert joined == payload
    assert part_ranges(10, 300) == [(0, 10)]
    assert part_name("a.zip", 1, 1) == "a.zip"


def test_incompressible_big_files_are_stored_not_deflated(tmp_path):
    rnd = os.urandom(20 * 1024 * 1024)            # e.g. a .bak made WITH COMPRESSION
    zeros = b"\0" * (20 * 1024 * 1024)            # e.g. a normal .bak
    art = Artifact(label="", entries=[_entry("compressed.bak", rnd), _entry("plain.bak", zeros)])
    out = tmp_path / "a.zip"
    build_zip(art, out, password="pw")
    with pyzipper.AESZipFile(out) as z:
        z.setpassword(b"pw")
        info = {i.filename: i for i in z.infolist()}
        assert z.read("compressed.bak") == rnd and z.read("plain.bak") == zeros
    # AES members record the real method in the extra field; compare stored sizes instead
    assert info["compressed.bak"].compress_size >= len(rnd)
    assert info["plain.bak"].compress_size < len(zeros) // 100
