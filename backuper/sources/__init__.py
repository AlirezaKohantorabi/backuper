from __future__ import annotations

from backuper.sources.base import COMMON_FIELDS, RunContext, SourceError, SourceType
from backuper.sources.cpanel import CPanel
from backuper.sources.directadmin import DirectAdmin
from backuper.sources.mikrotik import MikroTik
from backuper.sources.mssql_collect import MSSQLCollect
from backuper.sources.mssql_trigger import MSSQLTrigger
from backuper.sources.remote_folder import RemoteFolder
from backuper.sources.smb_folder import SMBFolder

# Order here = order in the bot's "Add source" menu.
REGISTRY: dict[str, type[SourceType]] = {
    cls.type_id: cls
    for cls in (MSSQLCollect, MSSQLTrigger, MikroTik, SMBFolder, RemoteFolder, CPanel, DirectAdmin)
}


def get_type(type_id: str) -> type[SourceType]:
    try:
        return REGISTRY[type_id]
    except KeyError:
        raise SourceError(f"Unknown source type '{type_id}'") from None


def credential_keys_by_type() -> dict[str, set[str]]:
    return {t: cls.credential_keys() for t, cls in REGISTRY.items()}


__all__ = ["COMMON_FIELDS", "REGISTRY", "RunContext", "SourceError", "SourceType", "credential_keys_by_type",
           "get_type"]
