"""SMB robustness: own connection per client, waiting for credits, never a silent short copy."""

from __future__ import annotations

import io
import threading
import time

import pytest
import smbclient._io as smb_io
from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBException

from backuper.transport.smb import SMB, ResumableReader
from backuper.util import IncompleteCopy, check_complete


def test_credit_guard_is_installed():
    assert getattr(Connection._send, "_backuper_guard", False)


class _Conn:
    """Just enough of a Connection for smbclient's chunk-size calculation."""

    def __init__(self, credits: int):
        self.sequence_lock = threading.Lock()
        self.sequence_window = {"low": 0, "high": credits}
        self.max_read_size = self.max_write_size = 8 * 1024 * 1024


def test_chunk_size_waits_for_credits_instead_of_returning_zero():
    conn = _Conn(0)
    # Without the guard a 0-credit read becomes a 0-byte read, which callers take for end-of-file.
    threading.Timer(0.2, lambda: conn.sequence_window.__setitem__("high", 64)).start()
    t0 = time.monotonic()
    size, _ = smb_io._chunk_size(conn, 4 * 1024 * 1024, "read")
    assert size == 4 * 1024 * 1024 and time.monotonic() - t0 >= 0.15


def test_each_client_has_its_own_connections():
    a, b = SMB("u", "p"), SMB("u", "p")
    assert a._base_kw()["connection_cache"] is not b._base_kw()["connection_cache"]
    a._cache["x"] = object()  # close() must cope with whatever is cached and empty it
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("smbclient.reset_connection_cache", lambda **kw: kw["connection_cache"].clear())
        with a:
            pass
    assert a._cache == {}


def test_check_complete():
    data = io.BytesIO(b"0123456789")
    check_complete(data, 10, "f")                     # all read: fine
    with pytest.raises(IncompleteCopy):
        check_complete(io.BytesIO(b"0123456789"), 4, "f")

    class Pipe(io.RawIOBase):  # can't tell its size: trusted
        def seekable(self):
            return False
    check_complete(Pipe(), 0, "pipe")


class _Flaky(io.BytesIO):
    """A file whose connection 'drops' once after `fail_at` bytes."""

    def __init__(self, data: bytes, fail_at: int | None):
        super().__init__(data)
        self.fail_at = fail_at

    def readinto(self, b):
        if self.fail_at is not None and self.tell() >= self.fail_at:
            raise SMBException("Connection timed out. Server did not respond within timeout.")
        n = min(len(b), 1000)
        return super().readinto(memoryview(b)[:n])


def test_reader_resumes_at_the_same_byte_after_a_dropped_connection():
    data = bytes(range(256)) * 40  # 10 KB
    opens, resets = [], []

    def open_raw():
        opens.append(1)
        return _Flaky(data, 3000 if len(opens) == 1 else None)

    reader = io.BufferedReader(ResumableReader(open_raw, lambda: resets.append(1), "f", waits=(0, 0),
                                               identity=lambda raw: (len(raw.getvalue()),)), buffer_size=4096)
    got = reader.read()
    assert got == data and len(opens) == 2 and len(resets) == 1   # reconnected once, nothing re-read from 0
    check_complete(reader, len(got), "f")


def test_reader_refuses_to_continue_a_file_that_changed():
    opens = []

    def open_raw():
        opens.append(1)
        return _Flaky(b"x" * 5000 if len(opens) == 1 else b"y" * 6000, 2000 if len(opens) == 1 else None)

    reader = ResumableReader(open_raw, lambda: None, "f", waits=(0,), identity=lambda raw: (len(raw.getvalue()),))
    with pytest.raises(IncompleteCopy, match="changed"):
        while reader.read(1000):
            pass


def test_reader_gives_up_on_permanent_errors():
    def open_raw():
        raw = _Flaky(b"x" * 100, 0)
        raw.readinto = lambda b: (_ for _ in ()).throw(PermissionError("STATUS_ACCESS_DENIED"))
        return raw

    reader = ResumableReader(open_raw, lambda: None, "f", waits=(0, 0, 0), identity=lambda raw: ())
    with pytest.raises(PermissionError):
        reader.read(10)
