from __future__ import annotations

import asyncio
import time
from typing import Any

from backuper.fields import BOOL, CHOICE, CREDENTIAL, INT, LIST, UNC, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType
from backuper.sources.mssql_common import (
    GROUP_MODES, apply_retention, check_fresh, group_files, label_for, signature, wait_until_stable,
)
from backuper.util import human_size


class MSSQLCollect(SourceType):
    type_id = "mssql_collect"
    title = "SQL Server — collect .bak from share"
    category = "databases"
    description = ("Picks up the newest .bak file(s) that a SQL Agent job / maintenance plan wrote to a "
                   "shared folder, and deletes old ones beyond 'keep last'.")
    fields = [
        Field("share", "Backup folder (network path)", UNC, example=r"\\172.16.3.251\SQLBackup",
              help="The shared folder SQL Server writes its .bak files to."),
        Field("credential", "Share credential", CREDENTIAL),
        Field("patterns", "File patterns", LIST, default=["*.bak"],
              help="Which files count as backups. With a '/' the pattern matches the sub-folder path too "
                   "(e.g. ArpaEn/*.bak).",
              example="Cash_*.bak, Project_*.bak"),
        Field("group_by", "One backup per", CHOICE, default="pattern", choices=GROUP_MODES,
              help="pattern: newest file of each pattern. database: newest file per database name found in "
                   "the file names (Holoo1_…, Holoo2_…). folder: newest file per sub-folder (Arpa\\, ArpaEn\\). "
                   "'Keep last N' then applies to each group separately."),
        Field("recursive", "Include sub-folders", BOOL, default=False),
        Field("keep_last", "Keep last N on server", INT, default=0, min=0, max=1000,
              help="After a successful backup, delete older .bak files so only N remain per database. 0 = never delete."),
        Field("max_age_hours", "Max age (hours)", INT, default=26, min=0, max=24 * 60,
              help="Fail (and alert) if the newest .bak is older than this — usually means the SQL job broke. 0 = off."),
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        return f"{config.get('share', '?')} · keep {config.get('keep_last') or '∞'}"

    def _smb(self):
        return self.ctx.smb(self.cfg["credential"])

    async def _groups(self):
        smb = self._smb()
        files = await asyncio.to_thread(smb.list_files, self.cfg["share"], recursive=bool(self.cfg.get("recursive")))
        mode = self.cfg.get("group_by") or ("database" if self.cfg.get("split_by_database") else "pattern")
        groups = group_files(files, self.cfg.get("patterns") or ["*.bak"], mode)
        self.ctx.log.info("listed %s: %d file(s), %d group(s) by %s", self.cfg["share"], len(files), len(groups), mode)
        for key, fs in groups.items():
            self.ctx.log.debug("group %s: %d file(s), newest %s (%d bytes, %s)", key, len(fs), fs[0].name, fs[0].size,
                               time.strftime("%Y-%m-%d %H:%M", time.localtime(fs[0].mtime)))
        return smb, groups

    async def test(self) -> str:
        smb, groups = await self._groups()
        if not groups:
            return f"Connected to {self.cfg['share']}, but no files match {self.cfg.get('patterns')}."
        lines = [f"Connected to {self.cfg['share']}. Will send:"]
        for key, files in groups.items():
            f = files[0]
            lines.append(f"• {label_for(key, f)}: {f.name} ({human_size(f.size)}), {len(files)} file(s) in total")
        return "\n".join(lines)

    async def collect(self) -> list[Artifact]:
        smb, groups = await self._groups()
        if not groups:
            raise SourceError(f"no files matching {self.cfg.get('patterns')} in {self.cfg['share']}")
        artifacts = []
        already = self.ctx.get_state("last_sent", {})
        for key, files in groups.items():
            newest = files[0]
            label = label_for(key, newest)
            check_fresh(newest, int(self.cfg.get("max_age_hours") or 0))
            if already.get(key) == signature(newest):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(newest.mtime))
                self.ctx.note(f"{label}: no new backup since {when} (already stored)")
                continue
            newest = await wait_until_stable(smb, newest, self.ctx)
            self.ctx.log.info("%s: taking %s (%d bytes, %s)", label, newest.name, newest.size,
                              time.strftime("%Y-%m-%d %H:%M", time.localtime(newest.mtime)))
            artifacts.append(Artifact(
                label=label,
                entries=[Entry(arcname=newest.name, size=newest.size, mtime=newest.mtime,
                               opener=smb.opener(newest.path))],
                meta={"group": key, "sig": signature(newest)},
            ))
        return artifacts

    async def after_success(self, artifacts: list[Artifact]) -> list[str]:
        # Remember what was fully delivered so an unchanged file isn't sent again next run
        # (if Telegram failed, it's retried automatically next run).
        sent = dict(self.ctx.get_state("last_sent", {}))
        sent.update({a.meta["group"]: a.meta["sig"] for a in artifacts if a.meta.get("sig") and a.meta.get("delivered")})
        self.ctx.set_state("last_sent", sent)
        keep = int(self.cfg.get("keep_last") or 0)
        if keep <= 0:
            return []
        smb, groups = await self._groups()
        stored = {a.meta["group"] for a in artifacts}
        total = 0
        for key in stored:
            total += await apply_retention(smb, groups.get(key, []), keep, self.ctx)
        return [f"retention: deleted {total} old file(s), keeping {keep}"] if total else []
