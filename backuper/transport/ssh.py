"""SSH/SFTP helpers on asyncssh with trust-on-first-use host key pinning."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import asyncssh

log = logging.getLogger(__name__)


class HostKeyChanged(RuntimeError):
    pass


def _with_legacy(defaults, supported, extra: list[str]) -> list[str]:
    out = [a.decode() if isinstance(a, bytes) else a for a in defaults]
    names = {a.decode() if isinstance(a, bytes) else a for a in supported}
    out += [a for a in extra if a in names and a not in out]
    return out


def _algorithm_options() -> dict:
    """Modern defaults plus the legacy algorithms old RouterOS v6 / old hosting SSH need."""
    try:
        from asyncssh.encryption import get_default_encryption_algs, get_encryption_algs
        from asyncssh.kex import get_default_kex_algs, get_kex_algs
        from asyncssh.mac import get_default_mac_algs, get_mac_algs
        from asyncssh.public_key import get_default_public_key_algs, get_public_key_algs
    except ImportError:  # pragma: no cover - very old/new asyncssh, use library defaults
        return {}
    return {
        "kex_algs": _with_legacy(get_default_kex_algs(), get_kex_algs(),
                                 ["diffie-hellman-group-exchange-sha1", "diffie-hellman-group14-sha1",
                                  "diffie-hellman-group1-sha1"]),
        "encryption_algs": _with_legacy(get_default_encryption_algs(), get_encryption_algs(),
                                        ["aes128-cbc", "aes256-cbc", "aes192-cbc", "3des-cbc"]),
        "mac_algs": _with_legacy(get_default_mac_algs(), get_mac_algs(), ["hmac-sha1", "hmac-md5"]),
        "server_host_key_algs": _with_legacy(get_default_public_key_algs(), get_public_key_algs(),
                                             ["ssh-rsa", "ssh-dss"]),
    }


@dataclass
class SSHTarget:
    host: str
    port: int
    username: str
    password: str
    pinned_key: str = ""  # OpenSSH public key line; empty = trust on first use
    timeout: int = 30


async def connect(target: SSHTarget) -> tuple[asyncssh.SSHClientConnection, str]:
    """Open a connection. Returns (conn, host_key_openssh).

    If a key is pinned, the server must present exactly that key — checked
    before we send the password. A mismatch raises HostKeyChanged.
    """
    known_hosts = None
    if target.pinned_key:
        try:
            known_hosts = ([asyncssh.import_public_key(target.pinned_key)], [], [])
        except (asyncssh.KeyImportError, ValueError):
            log.warning("Pinned host key for %s is unreadable; ignoring it", target.host)
    try:
        conn = await asyncssh.connect(
            target.host, port=target.port, username=target.username, password=target.password,
            known_hosts=known_hosts, client_keys=None, agent_path=None,
            connect_timeout=target.timeout, login_timeout=target.timeout, keepalive_interval=30,
            **_algorithm_options(),
        )
    except asyncssh.HostKeyNotVerifiable as e:
        raise HostKeyChanged(
            f"SSH host key of {target.host} changed! If the device was replaced on purpose, "
            "reset the pinned key from the source menu."
        ) from e
    key = conn.get_server_host_key()
    key_line = key.export_public_key("openssh").decode().strip() if key else ""
    return conn, key_line


async def run(conn: asyncssh.SSHClientConnection, command: str, timeout: int = 300) -> str:
    result = await conn.run(command, check=False, timeout=timeout)
    out = (result.stdout or "") if isinstance(result.stdout, str) else (result.stdout or b"").decode(errors="replace")
    err = (result.stderr or "") if isinstance(result.stderr, str) else (result.stderr or b"").decode(errors="replace")
    if result.exit_status not in (0, None):
        shown = re.sub(r"(password=)(\"[^\"]*\"|\S+)", r"\1***", command)  # never leak secrets into reports
        raise RuntimeError(f"`{shown}` failed (exit {result.exit_status}): {(err or out).strip()[:300]}")
    return out
