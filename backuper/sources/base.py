"""Base class and run context for source plugins.

To add a new kind of backup:
  1. create backuper/sources/<name>.py with a subclass of SourceType,
  2. list its `fields` and implement `collect()` (+ optionally `test()` and
     `after_success()`),
  3. add it to REGISTRY in backuper/sources/__init__.py.
The bot builds the add/edit forms from `fields` automatically.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import jdatetime

from backuper import jalali
from backuper.fields import BOOL, CREDENTIAL, GROUP, INT, SCHEDULE, TEXT, Field
from backuper.models import Artifact, Credential, SourceRecord
from backuper.transport.smb import SMB

if TYPE_CHECKING:
    from backuper.db import Store


class SourceError(RuntimeError):
    """An expected, explainable failure (shown to the user without a traceback)."""


COMMON_FIELDS: list[Field] = [
    Field("group", "Backup group", GROUP, required=False, default="",
          help="Sources in a group run together at the group's time and their files are uploaded smallest "
               "first, one after another. 'No group' = this source runs at its own time (or only manually)."),
    Field("schedule", "Own time", SCHEDULE, required=False, default="", show_if=("group", ("", None)),
          help="Only for sources outside a group: when this one runs by itself, e.g. 19:00 "
               "(or cron lines separated by ';'). Empty = manual only."),
    Field("telegram", "Send to Telegram", BOOL, default=True, show_if=("format", ("zip", None)),
          help="Off = keep it only in the archive (for data too big for Telegram). The zip is then written "
               "straight into the archive, without a temporary copy on this server."),
    Field("archive_keep", "Archive copies to keep", INT, default=0, min=0, max=3650,
          help="Keep only the newest N backups of this source in the archive. 0 = follow the global "
               "'Archive days to keep'. Useful for very large folders.",
          tags=frozenset({"advanced"})),
    Field("topic", "Telegram topic", TEXT, required=False, default="",
          help="Override the topic this source posts to (topic name). Empty = automatic.",
          tags=frozenset({"advanced"})),
]


class RunContext:
    def __init__(self, store: Store, source: SourceRecord, workdir: Path, now: jdatetime.datetime,
                 log: logging.Logger | None = None):
        self.store = store
        self.source = source
        self.workdir = workdir
        self.now = now
        self.log = log or logging.getLogger(f"backuper.source.{source.name}")
        self.warnings: list[str] = []
        self.notes: list[str] = []  # informational, e.g. "no new backup since …" (not a warning)
        self._smbs: list[SMB] = []
        workdir.mkdir(parents=True, exist_ok=True)

    @property
    def stamp(self) -> str:
        return jalali.stamp(self.now)

    def credential(self, name: str, *, what: str = "credential") -> Credential:
        if not name:
            raise SourceError(f"No {what} selected")
        cred = self.store.get_credential_by_name(name)
        if cred is None:
            raise SourceError(f"Credential '{name}' does not exist (deleted?)")
        return cred

    def smb(self, cred_name: str) -> SMB:
        """An SMB client with its own connection; closed by `close()` when the source is done."""
        c = self.credential(cred_name, what="SMB credential")
        client = SMB(c.username, c.password)
        self._smbs.append(client)
        return client

    def reset_connections(self) -> None:
        """Drop this source's SMB connections (they reconnect on next use) — before a retry."""
        for client in self._smbs:
            client.close()

    def close(self) -> None:
        """Close every SMB connection this source opened."""
        self.reset_connections()
        self._smbs.clear()

    def warn(self, msg: str) -> None:
        self.log.warning(msg)
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.log.info(msg)
        self.notes.append(msg)

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.source.state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.source.state[key] = value
        self.store.set_source_state(self.source.id, self.source.state)


class SourceType(ABC):
    type_id: ClassVar[str]
    title: ClassVar[str]
    category: ClassVar[str]           # databases | network | websites | files
    description: ClassVar[str] = ""
    fields: ClassVar[list[Field]] = []

    def __init__(self, record: SourceRecord, ctx: RunContext):
        self.record = record
        self.cfg = record.config
        self.ctx = ctx

    # -- schema helpers -------------------------------------------------------
    @classmethod
    def all_fields(cls) -> list[Field]:
        return [*cls.fields, *COMMON_FIELDS]

    @classmethod
    def secret_keys(cls) -> set[str]:
        return {f.key for f in cls.all_fields() if f.secret}

    @classmethod
    def credential_keys(cls) -> set[str]:
        return {f.key for f in cls.all_fields() if f.kind == CREDENTIAL}

    @classmethod
    def describe(cls, config: dict[str, Any]) -> str:
        """One-line summary shown in menus."""
        for key in ("host", "share", "path"):
            if config.get(key):
                return str(config[key])
        return cls.title

    # -- behaviour ---------------------------------------------------------------
    @abstractmethod
    async def collect(self) -> list[Artifact]:
        """Fetch/prepare data. Each Artifact becomes one zip."""

    async def test(self) -> str:
        """Check connectivity/permissions without making a backup. Returns a human summary."""
        return "No test available for this source type."

    async def after_success(self, artifacts: list[Artifact]) -> list[str]:
        """Called with the artifacts that were safely stored. Returns notes (e.g. retention)."""
        return []
