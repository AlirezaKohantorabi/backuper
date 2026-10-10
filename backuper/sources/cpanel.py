from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from backuper.fields import BOOL, CHOICE, CREDENTIAL, HOST, INT, TEXT, Field
from backuper.models import Artifact
from backuper.sources.base import SourceError, SourceType
from backuper.sources.hosting_common import (
    TRANSFER_FIELDS, download_and_delete, list_archives, open_transfer, pin_transfer_key, wait_new_archive_stable,
)


class CPanel(SourceType):
    type_id = "cpanel"
    title = "cPanel account (full backup)"
    category = "websites"
    description = ("Asks cPanel to generate a full account backup (files, databases, email), downloads it "
                   "over FTPS/SFTP and deletes it from the host immediately.")
    fields = [
        Field("host", "cPanel host", HOST, example="example.com", help="Domain or server hostname of the panel."),
        Field("port", "cPanel port", INT, default=2083, min=1, max=65535),
        Field("credential", "cPanel login", CREDENTIAL,
              help="Username + API token (cPanel → Security → Manage API Tokens) or the account password."),
        Field("auth", "Login type", CHOICE, default="token", choices=("token", "password")),
        Field("include_homedir", "Include home directory", BOOL, default=True,
              help="Off = databases, email & config only (much smaller)."),
        Field("remote_dir", "Where cPanel saves backups", TEXT, default="/",
              help="As seen by the download login. The account's home directory is '/' for the main FTP user."),
        *TRANSFER_FIELDS,
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        return f"{config.get('host', '?')} (cPanel)"

    # -- API ------------------------------------------------------------------
    def _client(self) -> tuple[httpx.AsyncClient, str]:
        cred = self.ctx.credential(self.cfg["credential"], what="cPanel login")
        base = f"https://{self.cfg['host']}:{int(self.cfg.get('port') or 2083)}"
        headers, auth = {}, None
        if self.cfg.get("auth", "token") == "token":
            headers["Authorization"] = f"cpanel {cred.username}:{cred.password}"
        else:
            auth = (cred.username, cred.password)
        client = httpx.AsyncClient(base_url=base, headers=headers, auth=auth, timeout=60,
                                   verify=bool(self.cfg.get("verify_tls", True)))
        return client, cred.username

    async def _uapi(self, client: httpx.AsyncClient, module: str, func: str, **params) -> Any:
        r = await client.get(f"/execute/{module}/{func}", params=params)
        if r.status_code in (401, 403):
            raise SourceError("cPanel rejected the login (check username/token and 'Login type')")
        r.raise_for_status()
        data = r.json()
        if not data.get("status"):
            raise SourceError(f"cPanel {module}::{func}: {'; '.join(data.get('errors') or ['unknown error'])}")
        return data.get("data")

    async def _list_full_backups(self, client: httpx.AsyncClient, user: str) -> list[dict] | None:
        """API2 Backups::listfullbackups reports 'inprogress'/'complete'. None if unavailable."""
        try:
            r = await client.get("/json-api/cpanel", params={
                "cpanel_jsonapi_user": user, "cpanel_jsonapi_apiversion": "2",
                "cpanel_jsonapi_module": "Backups", "cpanel_jsonapi_func": "listfullbackups",
            })
            r.raise_for_status()
            return r.json()["cpanelresult"]["data"]
        except Exception:  # noqa: BLE001 - fall back to watching the file
            return None

    # -- plugin API -------------------------------------------------------------
    async def test(self) -> str:
        client, user = self._client()
        async with client:
            await self._uapi(client, "Backup", "list_backups")
        async with open_transfer(self.ctx, self.cfg, self.cfg["credential"]) as fs:
            pin_transfer_key(self.ctx, fs)
            existing = await list_archives(fs, self.cfg.get("remote_dir") or "/")
        note = f" ⚠️ {len(existing)} old backup archive(s) are sitting on the host." if existing else ""
        return f"cPanel API OK, {self.cfg.get('transfer', 'ftps').upper()} login OK.{note}"

    async def collect(self) -> list[Artifact]:
        remote_dir = self.cfg.get("remote_dir") or "/"
        timeout_s = int(self.cfg.get("timeout_minutes") or 180) * 60
        async with open_transfer(self.ctx, self.cfg, self.cfg["credential"]) as fs:
            pin_transfer_key(self.ctx, fs)
            before = set(await list_archives(fs, remote_dir))

        client, user = self._client()
        async with client:
            await self._uapi(client, "Backup", "fullbackup_to_homedir",
                             homedir="include" if self.cfg.get("include_homedir", True) else "skip")
            self.ctx.log.info("cPanel backup started on %s", self.cfg["host"])
            name = await self._wait_via_api(client, user, before, timeout_s)

        # Fresh transfer session: FTP servers drop control connections that sat idle during a long backup.
        async with open_transfer(self.ctx, self.cfg, self.cfg["credential"]) as fs:
            if name is None:  # API2 not available: watch the file instead
                name = (await wait_new_archive_stable(fs, remote_dir, before, self.ctx, timeout_s)).name
            return [await download_and_delete(fs, remote_dir, name, self.ctx)]

    async def _wait_via_api(self, client, user: str, before: set[str], timeout_s: int) -> str | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(30)
            items = await self._list_full_backups(client, user)
            if items is None:
                return None
            new = [i for i in items if i.get("file") and i["file"] not in before]
            if not new:
                continue
            newest = max(new, key=lambda i: int(i.get("time") or 0))
            if str(newest.get("status", "")).lower() == "complete":
                return newest["file"]
            self.ctx.log.info("cPanel backup %s: %s", newest["file"], newest.get("status"))
        raise SourceError(f"cPanel backup didn't finish within {timeout_s // 60} min")
