from __future__ import annotations

import asyncio
import ntpath
import re
import time
from typing import Any

import pymssql

from backuper.fields import BOOL, CHOICE, CREDENTIAL, HOST, INT, LIST, TEXT, UNC, Field
from backuper.models import Artifact, Entry
from backuper.sources.base import SourceError, SourceType
from backuper.transport.smb import join as unc_join
from backuper.util import safe_filename

SYSTEM_DBS = {"master", "model", "msdb", "tempdb"}


def quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


class MSSQLTrigger(SourceType):
    type_id = "mssql_trigger"
    title = "SQL Server — run BACKUP + collect"
    category = "databases"
    description = ("Connects to SQL Server, runs BACKUP DATABASE (COPY_ONLY by default, so it never breaks "
                   "existing backup chains), then collects the .bak from the share. No SQL Agent job needed; "
                   "works on Express.")
    fields = [
        Field("host", "SQL Server host", HOST, example="172.16.3.251"),
        Field("port", "Port", INT, default=1433, min=1, max=65535),
        Field("instance", "Instance name", TEXT, required=False, default="",
              help="Only for named instances (e.g. SQLEXPRESS). Then the port is resolved via SQL Browser."),
        Field("sql_credential", "SQL login", CREDENTIAL,
              help="SQL login (or DOMAIN\\user) with db_backupoperator on the databases."),
        Field("databases", "Databases", LIST, example="Hamrahan, Hamrahan_Archive",
              help="Database names, or * for every online user database."),
        Field("server_dir", "Folder on the SQL server", TEXT, example=r"D:\SQLBackup",
              help="Local path ON THE SQL SERVER where SQL Server writes the .bak (its service account needs write)."),
        Field("share", "Same folder as network path", UNC, example=r"\\172.16.3.251\SQLBackup",
              help="The network share pointing to that same folder."),
        Field("share_credential", "Share credential", CREDENTIAL),
        Field("copy_only", "COPY_ONLY", BOOL, default=True,
              help="Recommended: doesn't disturb differential/log backup chains."),
        Field("compression", "SQL backup compression", CHOICE, default="auto", choices=("auto", "on", "off"),
              help="auto = on where the edition supports it (Enterprise/Standard/Developer), off on Express. "
                   "Compressed backups are ~5x smaller and faster to copy."),
        Field("keep_last", "Keep last N on server", INT, default=7, min=0, max=1000,
              help="Old backups are deleted beyond N per database. 0 = never delete."),
        Field("retention_scope", "Keep last N counts", CHOICE, default="mine", choices=("mine", "all"),
              help="mine: only backups this bot made. all: every <database>_*.bak in the folder — also the ones "
                   "the application makes itself (they are deleted too beyond N)."),
        Field("encryption", "Connection encryption", CHOICE, default="auto", choices=("auto", "off", "require"),
              help="auto works for SQL Server 2014+. 'off' is needed for old servers (SQL 2008/2012 without "
                   "TLS 1.2 updates) — the login then crosses the LAN unencrypted.",
              tags=frozenset({"advanced"})),
        Field("timeout_minutes", "Timeout (minutes)", INT, default=180, min=1, max=24 * 60),
    ]

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        host = config.get("host", "?")
        if config.get("instance"):
            host += "\\" + config["instance"]
        return f"{host} · {', '.join(config.get('databases') or [])}"

    # -- SQL --------------------------------------------------------------------
    def _connect(self) -> pymssql.Connection:
        cred = self.ctx.credential(self.cfg["sql_credential"], what="SQL login")
        kw: dict[str, Any] = {
            "user": cred.username, "password": cred.password, "login_timeout": 20,
            "timeout": int(self.cfg.get("timeout_minutes") or 180) * 60,
            "autocommit": True, "appname": "backuper", "charset": "UTF-8",
        }
        if self.cfg.get("encryption") in ("off", "require"):
            kw["encryption"] = self.cfg["encryption"]
        if self.cfg.get("instance"):
            kw["server"] = f"{self.cfg['host']}\\{self.cfg['instance']}"
        else:
            kw["server"] = self.cfg["host"]
            kw["port"] = str(self.cfg.get("port") or 1433)
        try:
            return pymssql.connect(**kw)
        except pymssql.Error as e:
            hint = ""
            if "TDS server connection failed" in str(e) and self.cfg.get("encryption", "auto") == "auto":
                hint = " (old SQL Server? try 'Connection encryption' = off)"
            raise SourceError(f"can't connect to SQL Server {kw['server']}: {_sql_err(e)}{hint}") from e

    def _list_dbs(self, conn) -> list[str]:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sys.databases WHERE state_desc='ONLINE' AND source_database_id IS NULL "
                    "ORDER BY name")
        return [r[0] for r in cur.fetchall()]

    def _resolve_dbs(self, conn) -> list[str]:
        wanted = self.cfg.get("databases") or []
        online = self._list_dbs(conn)
        if wanted == ["*"]:
            return [d for d in online if d.lower() not in SYSTEM_DBS]
        lookup = {d.lower(): d for d in online}
        missing = [w for w in wanted if w.lower() not in lookup]
        if missing:
            raise SourceError(f"database(s) not found or offline: {', '.join(missing)}")
        return [lookup[w.lower()] for w in wanted]

    def _resolve_dbs_sync(self) -> list[str]:
        with self._connect() as conn:
            return self._resolve_dbs(conn)

    def _compress(self, conn) -> bool:
        mode = self.cfg.get("compression", "auto")
        if mode is True or mode == "on":
            return True
        if mode == "off":
            return False
        # "auto" (and False, the old default): only where the edition supports backup compression
        cur = conn.cursor()
        cur.execute("SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(128))")
        edition = (cur.fetchone()[0] or "").lower()
        return any(e in edition for e in ("enterprise", "standard", "developer", "business intelligence"))

    def _backup_sync(self, db: str, server_path: str) -> None:
        opts = ["INIT", "CHECKSUM", "STATS = 25"]
        if self.cfg.get("copy_only", True):
            opts.append("COPY_ONLY")
        with self._connect() as conn:
            if self._compress(conn):
                opts.append("COMPRESSION")
        sql = f"BACKUP DATABASE {quote_ident(db)} TO DISK = %s WITH {', '.join(opts)}"
        with self._connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (server_path,))
                while cur.nextset():
                    pass
            except pymssql.Error as e:
                raise SourceError(f"BACKUP DATABASE {db} failed: {_sql_err(e)}") from e

    # -- plugin API ----------------------------------------------------------------
    async def test(self) -> str:
        def probe():
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)), "
                            "CAST(SERVERPROPERTY('Edition') AS nvarchar(128))")
                ver, edition = cur.fetchone()
                return ver, edition, self._list_dbs(conn), self._resolve_dbs(conn), self._compress(conn)
        ver, edition, all_dbs, chosen, compress = await asyncio.to_thread(probe)
        smb = self.ctx.smb(self.cfg["share_credential"])
        await asyncio.to_thread(smb.list_files, self.cfg["share"])
        user_dbs = [d for d in all_dbs if d.lower() not in SYSTEM_DBS]
        return (f"SQL Server {ver} ({edition}).\nUser databases: {', '.join(user_dbs) or '—'}\n"
                f"Will back up: {', '.join(chosen)} (backup compression: {'on' if compress else 'off'})\n"
                f"Share {self.cfg['share']} is readable.")

    async def collect(self) -> list[Artifact]:
        dbs = await asyncio.to_thread(self._resolve_dbs_sync)
        smb = self.ctx.smb(self.cfg["share_credential"])
        artifacts = []
        for db in dbs:
            file_name = f"{safe_filename(db)}_{self.ctx.stamp}.bak"
            server_path = ntpath.join(self.cfg["server_dir"], file_name)
            started = time.monotonic()
            self.ctx.log.info("BACKUP DATABASE %s -> %s", db, server_path)
            await asyncio.to_thread(self._backup_sync, db, server_path)
            share_path = unc_join(self.cfg["share"], file_name)
            try:
                st = await asyncio.to_thread(smb.stat, share_path)
            except OSError as e:
                raise SourceError(
                    f"backup of {db} finished but {share_path} isn't visible — does the share point to "
                    f"{self.cfg['server_dir']}?"
                ) from e
            self.ctx.log.info("%s backed up in %.0fs", db, time.monotonic() - started)
            artifacts.append(Artifact(
                label=db,
                entries=[Entry(arcname=file_name, size=st.st_size, mtime=st.st_mtime, opener=smb.opener(share_path))],
                meta={"db": db},
            ))
        return artifacts

    async def after_success(self, artifacts: list[Artifact]) -> list[str]:
        keep = int(self.cfg.get("keep_last") or 0)
        if keep <= 0:
            return []
        from backuper.sources.mssql_common import apply_retention

        smb = self.ctx.smb(self.cfg["share_credential"])
        files = await asyncio.to_thread(smb.list_files, self.cfg["share"])
        total = 0
        scope_all = self.cfg.get("retention_scope") == "all"
        for a in artifacts:
            prefix = re.escape(safe_filename(a.meta["db"]) + "_")
            # mine: only files this source created (<db>_<yyyy-mm-dd>_<hhmm>.bak); all: any <db>_*.bak
            rx = re.compile(prefix + (r".*\.bak$" if scope_all else r"\d{4}-\d{2}-\d{2}_\d{4}\.bak$"),
                            re.IGNORECASE)
            mine = sorted((f for f in files if rx.match(f.name)), key=lambda f: f.mtime, reverse=True)
            total += await apply_retention(smb, mine, keep, self.ctx)
        return [f"retention: deleted {total} old file(s), keeping {keep}"] if total else []


def _sql_err(e: Exception) -> str:
    args = getattr(e, "args", ())
    if args and isinstance(args[0], tuple) and len(args[0]) >= 2:
        msg = args[0][1]
        msg = msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
        # FreeTDS appends "DB-Lib error message ..." noise
        return msg.split("DB-Lib error")[0].strip()
    return str(e)
