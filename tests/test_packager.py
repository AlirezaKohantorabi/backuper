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


def test_short_read_fails_instead_of_making_a_truncated_zip(tmp_path):
    class Truncating(io.BytesIO):  # "end of file" after 3 of its 10 bytes, like a 0-credit SMB read
        def read(self, n=-1):
            return super().read(3) if self.tell() < 3 else b""

    art = Artifact(label="", entries=[Entry(arcname="f.bin", size=10, mtime=1.7e9,
                                            opener=lambda: Truncating(b"0123456789"))])
    with pytest.raises(PackagingError, match="incomplete"):
        build_zip(art, tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_hashes_are_taken_while_zipping_and_checked_after_unzipping(tmp_path):
    import hashlib

    from backuper.packager import IntegrityError, checksum_file, parse_checksums, verify_zip

    data = {"a.bak": os.urandom(300_000), "sub/b.txt": b"hello " * 1000}
    art = Artifact(label="", entries=[Entry(arcname=n, size=len(d), mtime=1.7e9, opener=lambda d=d: io.BytesIO(d))
                                      for n, d in data.items()])
    hashes: dict[str, str] = {}
    build_zip(art, tmp_path / "x.zip", password="pw", hashes=hashes)
    assert hashes == {n: hashlib.sha256(d).hexdigest() for n, d in data.items()}
    assert parse_checksums(checksum_file(hashes)) == hashes

    with open(tmp_path / "x.zip", "rb") as f:
        assert verify_zip(f, hashes, password="pw") == 2
    with open(tmp_path / "x.zip", "rb") as f, pytest.raises(IntegrityError, match="differs"):
        verify_zip(f, {**hashes, "a.bak": "0" * 64}, password="pw")          # a changed file
    with open(tmp_path / "x.zip", "rb") as f, pytest.raises(IntegrityError, match="missing"):
        verify_zip(f, {**hashes, "c.bak": "0" * 64}, password="pw")          # a file that should be there
    with open(tmp_path / "x.zip", "rb") as f, pytest.raises(IntegrityError):
        verify_zip(f, hashes, password="wrong")
    raw = bytearray((tmp_path / "x.zip").read_bytes())
    raw[2000] ^= 0xFF                                                        # one flipped bit on disk
    with pytest.raises(IntegrityError):
        verify_zip(io.BytesIO(bytes(raw)), hashes, password="pw")
