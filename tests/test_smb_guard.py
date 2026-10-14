"""SMB robustness: own connection per client, waiting for credits, never a silent short copy."""

from __future__ import annotations

import io
import threading
import time

import pytest
import smbclient._io as smb_io
from smbprotocol.connection import Connection

from backuper.transport.smb import SMB
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
