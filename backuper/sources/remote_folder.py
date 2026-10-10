from __future__ import annotations

import posixpath
from typing import Any

from backuper.fields import BOOL, CHOICE, CREDENTIAL, HOST, INT, LIST, TEXT, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType
from backuper.transport.remote import DEFAULT_PORTS, PROTOCOLS, open_remote
from backuper.util import human_size, match_any, safe_filename


class RemoteFolder(SourceType):
    type_id = "remote_folder"
    title = "Folder over SFTP / FTP / FTPS"
    category = "files"
    description = "Downloads a folder from a Linux server, NAS or hosting account and zips it."
    fields = [
        Field("protocol", "Protocol", CHOICE, default="sftp", choices=PROTOCOLS),
        Field("host", "Host", HOST, example="10.0.0.20"),
        Field("port", "Port", INT, default=0, min=0, max=65535, help="0 = protocol default (22 / 21)."),
        Field("credential", "Login", CREDENTIAL),
        Field("remote_path", "Remote folder", TEXT, example="/var/backups/app"),
        Field("exclude", "Exclude patterns", LIST, required=False, default=[]),
        Field("delete_after", "Delete remote files after", BOOL, default=False,
              help="Move semantics: remove the downloaded files from the server once they're safely stored."),
        Field("verify_tls", "Verify TLS certificate", BOOL, default=True, show_if=("protocol", "ftps")),
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        return f"{config.get('protocol', '?')}://{config.get('host', '?')}{config.get('remote_path', '')}"

    def _fs(self):
        cred = self.ctx.credential(self.cfg["credential"], what="login")
        proto = self.cfg.get("protocol", "sftp")
        return open_remote(proto, self.cfg["host"], int(self.cfg.get("port") or DEFAULT_PORTS[proto]),
                           cred.username, cred.password, verify_tls=bool(self.cfg.get("verify_tls", True)),
                           pinned_key=self.ctx.get_state("host_key", ""))

    def _pin(self, fs) -> None:
        key = getattr(fs, "host_key", "")
        if key and not self.ctx.get_state("host_key"):
            self.ctx.set_state("host_key", key)

    def _filter(self, files):
        excl = self.cfg.get("exclude") or []
        return [f for f in files if not excl or not match_any(f.name, excl)]

    async def test(self) -> str:
        async with self._fs() as fs:
            self._pin(fs)
            files = self._filter(await fs.walk_files(self.cfg["remote_path"]))
        return f"Connected. {len(files)} files, {human_size(sum(f.size for f in files))}."

    async def collect(self) -> list[Artifact]:
        root = self.cfg["remote_path"].rstrip("/") or "/"
        base = safe_filename(posixpath.basename(root) or self.record.name)
        async with self._fs() as fs:
            self._pin(fs)
            files = self._filter(await fs.walk_files(root))
            if not files:
                raise SourceError(f"{root} is empty")
            entries, local_paths = [], []
            for f in files:
                rel = posixpath.relpath(f.path, root)
                local = self.ctx.workdir / "dl" / rel
                await fs.download(f.path, local)
                local_paths.append(local)
                entries.append(Entry.from_path(local, arcname=f"{base}/{rel}"))
        return [Artifact(label="", entries=entries, cleanup=local_paths,
                         meta={"remote_files": [f.path for f in files]})]

    async def after_success(self, artifacts: list[Artifact]) -> list[str]:
        if not self.cfg.get("delete_after"):
            return []
        n = 0
        async with self._fs() as fs:
            for a in artifacts:
                for p in a.meta.get("remote_files", []):
                    try:
                        await fs.remove(p)
                        n += 1
                    except Exception as e:  # noqa: BLE001
                        self.ctx.warn(f"couldn't delete {p}: {e}")
        return [f"deleted {n} remote file(s)"]
