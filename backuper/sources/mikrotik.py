from __future__ import annotations

import asyncio
import re
from typing import Any

from backuper.fields import BOOL, CREDENTIAL, HOST, INT, SECRET, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType
from backuper.transport import ssh

_KV = re.compile(r"^\s*([\w-]+):\s*(.*?)\s*$")
# RouterOS prints command errors to stdout and still exits 0
_ROS_ERROR = re.compile(r"^(failure:|syntax error|bad command name|expected end of command|no such item|"
                        r"input does not match|invalid value|not enough permissions|ambiguous value)",
                        re.IGNORECASE | re.MULTILINE)


async def ros(conn, command: str, timeout: int = 300) -> str:
    out = await ssh.run(conn, command, timeout=timeout)
    m = _ROS_ERROR.search(out)
    if m:
        line = out[m.start():].splitlines()[0].strip()
        shown = re.sub(r"(password=)(\"[^\"]*\"|\S+)", r"\1***", command)  # never leak secrets into reports
        raise RuntimeError(f"`{shown}`: {line}")
    return out


def parse_print(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = _KV.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def ros_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


class MikroTik(SourceType):
    type_id = "mikrotik"
    title = "MikroTik router (export + binary backup)"
    category = "network"
    description = ("Over SSH: `/export show-sensitive` (.rsc) and `/system backup save` (.backup), downloaded "
                   "over SFTP and deleted from the router afterwards. The SSH host key is pinned on first use.")
    fields = [
        Field("host", "Router address", HOST, example="192.168.10.254"),
        Field("port", "SSH port", INT, default=22, min=1, max=65535),
        Field("credential", "Router user", CREDENTIAL,
              help="User whose group has: ssh, ftp, read, write, policy, test, sensitive."),
        Field("export", "Make .rsc export", BOOL, default=True),
        Field("verbose_export", "Verbose export", BOOL, default=False,
              help="Include default values too (bigger, useful for diffing)."),
        Field("backup", "Make .backup file", BOOL, default=True),
        Field("backup_password", "Backup file password", SECRET, required=False, default="",
              help="Encrypt the .backup itself. Empty = unencrypted .backup (the zip is AES-encrypted anyway)."),
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        what = [k for k in ("export", "backup") if config.get(k, True)]
        return f"{config.get('host', '?')} · {' + '.join(what)}"

    def _target(self) -> ssh.SSHTarget:
        cred = self.ctx.credential(self.cfg["credential"], what="router user")
        return ssh.SSHTarget(host=self.cfg["host"], port=int(self.cfg.get("port") or 22),
                             username=cred.username, password=cred.password,
                             pinned_key=self.ctx.get_state("host_key", ""))

    async def _connect(self):
        conn, key = await ssh.connect(self._target())
        if key and not self.ctx.get_state("host_key"):
            self.ctx.set_state("host_key", key)
            self.ctx.log.info("Pinned SSH host key for %s", self.cfg["host"])
        return conn

    async def _info(self, conn) -> dict[str, str]:
        res = parse_print(await ros(conn, "/system resource print"))
        ident = parse_print(await ros(conn, "/system identity print"))
        res["identity"] = ident.get("name", "")
        return res

    async def test(self) -> str:
        conn = await self._connect()
        try:
            info = await self._info(conn)
            clock = parse_print(await ros(conn, "/system clock print"))
            try:
                ntp = parse_print(await ros(conn, "/system ntp client print"))
            except RuntimeError:
                ntp = {}
            sftp = await conn.start_sftp_client()
            sftp.exit()
        finally:
            conn.close()
        ntp_state = ntp.get("enabled", "?")
        if ntp.get("status"):
            ntp_state += f" ({ntp['status']})"
        return (f"Connected: {info.get('identity') or '?'} — {info.get('board-name', '?')}, "
                f"RouterOS {info.get('version', '?')}, uptime {info.get('uptime', '?')}. SFTP OK.\n"
                f"Router clock: {clock.get('date', '?')} {clock.get('time', '?')} "
                f"({clock.get('time-zone-name', '?')}), NTP client enabled: {ntp_state}")

    async def collect(self) -> list[Artifact]:
        if not self.cfg.get("export", True) and not self.cfg.get("backup", True):
            raise SourceError("both export and backup are turned off")
        base = f"backuper-{self.ctx.stamp}"
        conn = await self._connect()
        local_files = []
        try:
            info = await self._info(conn)
            major = int((info.get("version") or "7").split(".")[0] or 7)
            wanted: list[str] = []

            if self.cfg.get("export", True):
                parts = ["/export"]
                if major >= 7:
                    parts.append("show-sensitive")
                if self.cfg.get("verbose_export"):
                    parts.append("verbose")
                parts.append(f"file={base}")
                await ros(conn, " ".join(parts), timeout=600)
                wanted.append(f"{base}.rsc")

            if self.cfg.get("backup", True):
                pw = self.cfg.get("backup_password") or ""
                cmd = f"/system backup save name={base} " + (f"password={ros_quote(pw)}" if pw else "dont-encrypt=yes")
                try:
                    await ros(conn, cmd, timeout=600)
                except RuntimeError as e:
                    if pw or "expected end of command" not in str(e):
                        raise
                    await ros(conn, f"/system backup save name={base}", timeout=600)  # RouterOS < 6.43
                wanted.append(f"{base}.backup")

            sftp = await conn.start_sftp_client()
            try:
                found = await self._wait_for_files(sftp, wanted)
                for remote in found:
                    local = self.ctx.workdir / remote.split("/")[-1]
                    await sftp.get(remote, str(local))
                    local_files.append(local)
                for remote in found:
                    try:
                        await sftp.remove(remote)
                    except Exception as e:  # noqa: BLE001
                        self.ctx.warn(f"couldn't delete {remote} from router: {e}")
            finally:
                sftp.exit()
        finally:
            conn.close()

        info_txt = self.ctx.workdir / "router-info.txt"
        info_txt.write_text(
            "".join(f"{k}: {v}\n" for k, v in info.items() if k in
                    ("identity", "board-name", "version", "architecture-name", "uptime", "cpu", "total-memory")),
            encoding="utf-8",
        )
        name = info.get("identity") or self.record.name
        entries = [Entry.from_path(p, arcname=f"{name}{p.suffix}") for p in local_files]
        entries.append(Entry.from_path(info_txt))
        return [Artifact(label="", entries=entries, cleanup=[*local_files, info_txt])]

    async def _wait_for_files(self, sftp, names: list[str], timeout: float = 120) -> list[str]:
        """Export/backup files appear asynchronously; some boards put them under flash/."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        sizes: dict[str, int] = {}
        while True:
            found = []
            for n in names:
                for candidate in (n, f"flash/{n}"):
                    try:
                        st = await sftp.stat(candidate)
                    except Exception:  # noqa: BLE001 - not there (yet)
                        continue
                    if st.size and sizes.get(candidate) == st.size:
                        found.append(candidate)
                    sizes[candidate] = st.size or 0
                    break
            if len(found) == len(names):
                return found
            if loop.time() > deadline:
                missing = [n for n in names if not any(f.endswith(n) for f in found)]
                raise SourceError(f"router never produced {', '.join(missing)}")
            await asyncio.sleep(2)
