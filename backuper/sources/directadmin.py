from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx

from backuper.fields import CREDENTIAL, HOST, INT, LIST, TEXT, Field
from backuper.models import Artifact
from backuper.sources.base import SourceError, SourceType
from backuper.sources.hosting_common import (
    TRANSFER_FIELDS, download_and_delete, list_archives, open_transfer, pin_transfer_key, wait_new_archive_stable,
)

ALL_ITEMS = ["domain", "subdomain", "email", "forwarder", "autoresponder", "vacation", "list",
             "emailsettings", "ftp", "ftpsettings", "database", "database_data", "email_data"]


class DirectAdmin(SourceType):
    type_id = "directadmin"
    title = "DirectAdmin account (full backup)"
    category = "websites"
    description = ("Asks DirectAdmin to create a user backup (site files, databases, email), downloads it "
                   "from ~/backups over FTPS/SFTP and deletes it from the host immediately.")
    fields = [
        Field("host", "DirectAdmin host", HOST, example="example.com"),
        Field("port", "DirectAdmin port", INT, default=2222, min=1, max=65535),
        Field("credential", "DirectAdmin login", CREDENTIAL,
              help="User + password, or a Login Key (Account Manager → Login Keys) with CMD_API_SITE_BACKUP allowed."),
        Field("domain", "Domain", TEXT, example="example.com",
              help="Any domain of the account (the API requires one; the backup covers the whole account)."),
        Field("items", "What to include", LIST, default=ALL_ITEMS,
              help="DirectAdmin backup items. Default = everything."),
        Field("remote_dir", "Backups folder", TEXT, default="/backups",
              help="Where DirectAdmin writes user backups, as seen by the download login."),
        *TRANSFER_FIELDS,
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        return f"{config.get('domain') or config.get('host', '?')} (DirectAdmin)"

    def _client(self) -> httpx.AsyncClient:
        cred = self.ctx.credential(self.cfg["credential"], what="DirectAdmin login")
        return httpx.AsyncClient(base_url=f"https://{self.cfg['host']}:{int(self.cfg.get('port') or 2222)}",
                                 auth=(cred.username, cred.password), timeout=60,
                                 verify=bool(self.cfg.get("verify_tls", True)))

    @staticmethod
    def _parse(r: httpx.Response, what: str) -> dict[str, list[str]]:
        if r.status_code in (401, 403):
            raise SourceError("DirectAdmin rejected the login")
        r.raise_for_status()
        if r.text.lstrip().startswith("<"):
            raise SourceError(f"DirectAdmin {what}: got an HTML page — wrong port/login, or API access disabled")
        data = parse_qs(r.text)
        if data.get("error", ["0"])[0] != "0":
            detail = (data.get("details") or data.get("text") or ["unknown error"])[0]
            raise SourceError(f"DirectAdmin {what}: {detail}")
        return data

    async def test(self) -> str:
        async with self._client() as client:
            data = self._parse(await client.get("/CMD_API_SHOW_DOMAINS"), "login")
        domains = data.get("list[]", [])
        async with open_transfer(self.ctx, self.cfg, self.cfg["credential"]) as fs:
            pin_transfer_key(self.ctx, fs)
            existing = await list_archives(fs, self.cfg.get("remote_dir") or "/backups")
        note = f" ⚠️ {len(existing)} old backup archive(s) are sitting on the host." if existing else ""
        return f"DirectAdmin login OK (domains: {', '.join(domains) or '?'}). Download login OK.{note}"

    async def collect(self) -> list[Artifact]:
        remote_dir = self.cfg.get("remote_dir") or "/backups"
        timeout_s = int(self.cfg.get("timeout_minutes") or 180) * 60
        async with self._client() as client, open_transfer(self.ctx, self.cfg, self.cfg["credential"]) as fs:
            pin_transfer_key(self.ctx, fs)
            before = set(await list_archives(fs, remote_dir))
            form = {"action": "backup", "domain": self.cfg["domain"]}
            for i, item in enumerate(self.cfg.get("items") or ALL_ITEMS):
                form[f"select{i}"] = item
            self._parse(await client.post("/CMD_API_SITE_BACKUP", data=form), "create backup")
            self.ctx.log.info("DirectAdmin backup queued on %s", self.cfg["host"])
            entry = await wait_new_archive_stable(fs, remote_dir, before, self.ctx, timeout_s)
            return [await download_and_delete(fs, remote_dir, entry.name, self.ctx)]
