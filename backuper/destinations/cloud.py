"""Off-site copy of the archive's zips in the cloud (Google Drive, or any folder rclone can write to).

Replaces the Telegram uploads. After every run the newest `cloud_keep_days` date folders of the archive are
*mirrored*: each zip that isn't in the cloud yet (or has a different size) is uploaded — smallest first, one at
a time — and checked: the MD5 Google Drive computed must equal the MD5 of the file in the archive. Older date
folders are removed from the cloud. Because it compares with what's already there, an upload that failed (or a
day the internet was down) is simply done by the next run. Plain folder copies (Edari/Finance) are never sent —
only `*.zip` files directly in date folders — and a source can opt out with its *Send to cloud* switch.

The transfers are done by rclone (https://rclone.org), which is configured entirely through environment
variables built from the encrypted settings: no rclone config file with tokens on disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from backuper import jalali
from backuper.db import Store
from backuper.destinations.archive import Archive
from backuper.fields import unc_server
from backuper.util import human_duration, human_size, safe_filename, short_error

log = logging.getLogger(__name__)

DRIVE_SCOPE = "drive.file"  # rclone sees and deletes only the files it created itself
DRIVE_CHUNK = "64M"          # resumable-upload chunk size (bigger = faster, uses that much memory)
COMMAND_TIMEOUT = 6 * 3600   # one upload may take this long at most


class CloudError(RuntimeError):
    pass


def rclone_binary() -> str:
    """$RCLONE_BINARY, the copy installed next to this Python (.venv/bin/rclone, see install.sh), or PATH."""
    for candidate in (os.environ.get("RCLONE_BINARY"), str(Path(sys.executable).with_name("rclone")),
                      shutil.which("rclone")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise CloudError("rclone is not installed — run ./install.sh again")


def drive_authorize_command(client_id: str = "", client_secret: str = "") -> str:
    """The command to run on a PC with a browser to sign in to Google Drive (prints a token to paste back)."""
    import base64

    blob = {"scope": DRIVE_SCOPE}
    if client_id:
        blob.update(client_id=client_id, client_secret=client_secret)
    b64 = base64.urlsafe_b64encode(json.dumps(blob, separators=(",", ":")).encode()).decode().rstrip("=")
    return f'rclone authorize "drive" "{b64}"'


@dataclass
class SyncResult:
    uploaded: list[tuple[str, int]] = field(default_factory=list)     # (rel, size) uploaded and verified
    failed: dict[str, str] = field(default_factory=dict)              # rel -> error
    removed: list[str] = field(default_factory=list)                  # date folders removed from the cloud
    already: int = 0                                                  # zips that were already there
    seconds: float = 0.0

    def summary(self) -> str:
        parts = []
        if self.uploaded:
            parts.append(f"uploaded {len(self.uploaded)} zip(s), {human_size(sum(s for _, s in self.uploaded))}, "
                         f"all MD5-checked")
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED")
        if self.removed:
            parts.append(f"removed {', '.join(self.removed)}")
        if not parts:
            parts.append("up to date")
        return "☁ cloud: " + "; ".join(parts) + f" ({human_duration(self.seconds)})"


class Cloud:
    def __init__(self, store: Store, archive: Archive):
        self.store = store
        self.archive = archive

    # -- configuration -------------------------------------------------------------
    @property
    def kind(self) -> str:
        return (self.store.get_setting("cloud_type") or "off").strip()

    @property
    def enabled(self) -> bool:
        return self.kind != "off" and self.archive.enabled

    @property
    def folder(self) -> str:
        """Folder in the cloud (Drive: a folder name; local: an absolute path on this server)."""
        raw = (self.store.get_setting("cloud_folder") or "Backuper").strip()
        return raw.rstrip("/") if self.kind == "local" else raw.strip("/")

    @property
    def keep_days(self) -> int:
        return int(self.store.get_setting("cloud_keep_days") or 14)

    def _env(self, kind: str | None = None) -> dict[str, str]:
        """rclone remotes `cloud:` and `archive:` as environment variables (secrets never touch the disk).
        `kind` overrides the cloud_type setting (to test a sign-in before uploads are switched on)."""
        env = dict(os.environ, RCLONE_CONFIG=os.devnull)
        kind = kind or self.kind
        if kind == "drive":
            name = self.store.get_setting("cloud_credential") or ""
            cred = self.store.get_credential_by_name(name) if name else None
            if cred is None or not cred.password:
                raise CloudError("no Google Drive sign-in yet — run: backuper cloud login")
            data = json.loads(cred.password)
            token = data.get("token", data)  # stored as {"token": …, "client_id": …} (or a bare token)
            env.update(RCLONE_CONFIG_CLOUD_TYPE="drive", RCLONE_CONFIG_CLOUD_SCOPE=DRIVE_SCOPE,
                       RCLONE_CONFIG_CLOUD_TOKEN=json.dumps(token), RCLONE_CONFIG_CLOUD_USE_TRASH="false",
                       RCLONE_CONFIG_CLOUD_CHUNK_SIZE=DRIVE_CHUNK)
            if data.get("client_id"):
                env.update(RCLONE_CONFIG_CLOUD_CLIENT_ID=data["client_id"],
                           RCLONE_CONFIG_CLOUD_CLIENT_SECRET=data.get("client_secret", ""))
        elif kind == "local":
            env.update(RCLONE_CONFIG_CLOUD_TYPE="local")  # a folder on this server, e.g. a mounted USB disk
        else:
            raise CloudError(f"unknown cloud type {kind!r}")
        if self.archive.is_smb:
            cname = self.store.get_setting("archive_credential") or ""
            cred = self.store.get_credential_by_name(cname) if cname else None
            user = cred.username if cred else ""
            domain, _, user = user.rpartition("\\")
            env.update(RCLONE_CONFIG_ARCHIVE_TYPE="smb", RCLONE_CONFIG_ARCHIVE_HOST=unc_server(self.archive.root),
                       RCLONE_CONFIG_ARCHIVE_USER=user, RCLONE_CONFIG_ARCHIVE_DOMAIN=domain or "WORKGROUP",
                       RCLONE_CONFIG_ARCHIVE_PASS=self._obscure(cred.password if cred else ""))
        return env

    @staticmethod
    def _obscure(password: str) -> str:
        """rclone wants passwords in its own reversible 'obscured' form (read from stdin, not the command line)."""
        import subprocess

        out = subprocess.run([rclone_binary(), "obscure", "-"], input=password, capture_output=True, text=True,
                             timeout=30, env=dict(os.environ, RCLONE_CONFIG=os.devnull))
        if out.returncode:
            raise CloudError(f"rclone obscure failed: {out.stderr.strip()}")
        return out.stdout.strip()

    def _cloud_path(self, rel: str = "") -> str:
        """`cloud:<folder>/<rel>` (for 'local', the folder is a path on this server)."""
        return "cloud:" + "/".join(p for p in (self.folder, rel) if p)

    def _archive_path(self, rel: str) -> str:
        if self.archive.is_smb:
            parts = self.archive.root.replace("/", "\\").strip("\\").split("\\")[1:]  # share + sub folders
            return "archive:" + "/".join([*parts, rel])
        return str(Path(self.archive.root) / rel)

    # -- running rclone ------------------------------------------------------------
    async def _rclone(self, *args: str, env: dict[str, str] | None = None,
                      progress: Callable[[int], None] | None = None, timeout: float = 600) -> str:
        """Run rclone; returns stdout. With `progress`, parses rclone's JSON stats on stderr every few seconds."""
        cmd = [rclone_binary(), *args]
        if progress:
            cmd += ["--stats", "5s", "--use-json-log", "-v"]
        proc = await asyncio.create_subprocess_exec(*cmd, env=env or self._env(), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        errors: list[str] = []

        async def read_err() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    errors.append(line)
                    continue
                stats = rec.get("stats")
                if stats and progress:
                    progress(int(stats.get("bytes") or 0))
                elif rec.get("level") in ("error", "critical"):
                    errors.append(rec.get("msg", line))

        try:
            out, _ = await asyncio.wait_for(asyncio.gather(proc.stdout.read(), read_err()), timeout)  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            proc.kill()
            raise CloudError(f"rclone {args[0]} took longer than {human_duration(timeout)}") from None
        await proc.wait()
        if proc.returncode:
            raise CloudError(f"rclone {args[0]} failed: {'; '.join(errors[-3:]) or f'exit code {proc.returncode}'}")
        return out.decode(errors="replace")

    async def _list_cloud(self, env: dict[str, str]) -> dict[str, tuple[int, str]]:
        """{rel: (size, md5)} of everything already in the cloud folder."""
        try:
            raw = await self._rclone("lsjson", "-R", "--files-only", "--hash", "--hash-type", "md5",
                                     self._cloud_path(), env=env, timeout=600)
        except CloudError as e:
            if "directory not found" in str(e).lower():
                return {}
            raise
        return {i["Path"]: (int(i["Size"]), (i.get("Hashes") or {}).get("md5", "")) for i in json.loads(raw or "[]")}

    def _archive_md5(self, rel: str) -> str:
        """MD5 of a zip in the archive (read over the LAN)."""
        folder, name = rel.split("/", 1)
        h = hashlib.md5()  # noqa: S324 - matches what Google Drive reports, not used for security
        if self.archive.is_smb:
            from backuper.transport.smb import join as unc_join

            with self.archive._smb() as smb, smb.open_read(unc_join(self.archive._root_unc(), folder, name)) as f:  # noqa: SLF001
                while buf := f.read(4 * 1024 * 1024):
                    h.update(buf)
        else:
            with open(Path(self.archive.root) / folder / name, "rb") as f:
                while buf := f.read(4 * 1024 * 1024):
                    h.update(buf)
        return h.hexdigest()

    # -- the mirror ------------------------------------------------------------------
    async def wanted(self, excluded_sources: set[str]) -> list[tuple[str, int]]:
        """[(rel, size)] of the zips that should be in the cloud: in the newest `keep_days` date folders, not
        from a source that opted out."""
        prefixes = tuple(safe_filename(n) + "_" for n in excluded_sources)
        out = []
        for folder in (await self.archive.list_date_folders())[-self.keep_days:]:
            for name, size in await self.archive.list_zip_sizes(folder):
                if not prefixes or not name.startswith(prefixes):
                    out.append((f"{folder}/{name}", size))
        return out

    async def sync(self, excluded_sources: set[str], on_file: Callable[[int, int, str, int], None] | None = None,
                   progress: Callable[[int], None] | None = None) -> SyncResult:
        """Upload what's missing (smallest first, verified by MD5), then remove date folders older than
        `keep_days` from the cloud. `on_file(index, total, rel, size)` is called before each upload."""
        t0 = time.monotonic()
        res = SyncResult()
        env = await asyncio.to_thread(self._env)
        wanted = await self.wanted(excluded_sources)
        present = await self._list_cloud(env)
        todo = sorted(((rel, size) for rel, size in wanted if present.get(rel, (None,))[0] != size),
                      key=lambda x: x[1])
        res.already = len(wanted) - len(todo)
        if todo:
            log.info("cloud: %d zip(s) to upload, %s (%d already there)", len(todo),
                     human_size(sum(s for _, s in todo)), res.already)
        for i, (rel, size) in enumerate(todo, 1):
            if on_file:
                on_file(i, len(todo), rel, size)
            t1 = time.monotonic()
            try:
                await self._rclone("copyto", self._archive_path(rel), self._cloud_path(rel), "--retries", "3",
                                   "--low-level-retries", "20", env=env, progress=progress, timeout=COMMAND_TIMEOUT)
                local_md5 = await asyncio.to_thread(self._archive_md5, rel)
                cloud = await self._list_cloud_file(rel, env)
                if cloud != (size, local_md5):
                    await self._rclone("deletefile", self._cloud_path(rel), env=env)
                    raise CloudError(f"the cloud copy differs from the archive (size/MD5 {cloud} vs {size}/{local_md5})"
                                     " — deleted it, the next run uploads it again")
                res.uploaded.append((rel, size))
                took = time.monotonic() - t1
                log.info("cloud: uploaded %s (%s) in %s (%s/s), MD5 identical", rel, human_size(size),
                         human_duration(took), human_size(size / took if took else 0))
            except Exception as e:  # noqa: BLE001 - one file must not stop the others
                res.failed[rel] = short_error(e)
                log.error("cloud: upload of %s failed: %s", rel, short_error(e))
        res.removed = await self._prune(env, {rel.split("/", 1)[0] for rel, _ in wanted}
                                        | set((await self.archive.list_date_folders())[-self.keep_days:]))
        res.seconds = time.monotonic() - t0
        log.info("%s", res.summary())
        return res

    async def _list_cloud_file(self, rel: str, env: dict[str, str]) -> tuple[int, str]:
        raw = await self._rclone("lsjson", "--hash", "--hash-type", "md5", self._cloud_path(rel), env=env)
        items = json.loads(raw or "[]")
        if not items:
            return (-1, "")
        return int(items[0]["Size"]), (items[0].get("Hashes") or {}).get("md5", "")

    async def _prune(self, env: dict[str, str], keep: set[str]) -> list[str]:
        """Remove cloud date folders that are no longer among the newest `keep_days` (Jalali-named only)."""
        try:
            raw = await self._rclone("lsf", "--dirs-only", self._cloud_path(), env=env)
        except CloudError:
            return []
        removed = []
        for name in sorted(n.rstrip("/") for n in raw.splitlines() if n.strip()):
            if jalali.parse_folder(name) and name not in keep:
                await self._rclone("purge", self._cloud_path(name), env=env)
                log.info("cloud retention: removed %s", name)
                removed.append(name)
        return removed

    async def test(self, kind: str | None = None) -> str:
        """Upload a small file, check it, delete it; report the free space."""
        env = await asyncio.to_thread(self._env, kind)
        probe = f".backuper-test-{int(time.time())}.txt"
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / probe
        tmp.write_text("Backuper cloud test\n")
        try:
            await self._rclone("copyto", str(tmp), self._cloud_path(probe), env=env)
            size, md5 = await self._list_cloud_file(probe, env)
            ok = size == tmp.stat().st_size and md5 == hashlib.md5(tmp.read_bytes()).hexdigest()  # noqa: S324
            await self._rclone("deletefile", self._cloud_path(probe), env=env)
        finally:
            tmp.unlink(missing_ok=True)
        if not ok:
            raise CloudError("the test file came back different")
        about = ""
        try:
            info = json.loads(await self._rclone("about", "--json", "cloud:", env=env))
            if info.get("total"):
                about = f" Space: {human_size(info.get('used') or 0)} used of {human_size(info['total'])}" \
                        f" ({human_size(info.get('free') or 0)} free)."
        except (CloudError, ValueError):
            pass
        return f"Cloud ({kind or self.kind}) works: a test file was uploaded, checked and deleted.{about}"
