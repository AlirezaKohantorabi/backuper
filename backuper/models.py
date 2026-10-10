from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

# Item status values (per source per run)
OK, WARNING, PARTIAL, FAILED, SKIPPED = "ok", "warning", "partial", "failed", "skipped"
STATUS_ICON = {OK: "✅", WARNING: "⚠️", PARTIAL: "🟠", FAILED: "❌", SKIPPED: "⏭"}

# Categories decide the default Telegram topic
CATEGORY_TITLES = {
    "databases": "🗄 Databases",
    "network": "🌐 Network",
    "websites": "🌍 Websites",
    "files": "📁 File Servers",
    "reports": "📊 Reports",
}


@dataclass
class Credential:
    id: int
    name: str
    username: str
    password: str
    note: str = ""


@dataclass
class SourceRecord:
    id: int
    name: str
    type: str
    enabled: bool
    config: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class Entry:
    """One file that goes into a zip. `opener` returns a readable binary stream."""

    arcname: str
    size: int
    mtime: float
    opener: Callable[[], BinaryIO]
    compress: bool = True

    @classmethod
    def from_path(cls, path: Path, arcname: str | None = None, compress: bool | None = None) -> Entry:
        st = path.stat()
        name = arcname or path.name
        return cls(
            arcname=name,
            size=st.st_size,
            mtime=st.st_mtime,
            opener=lambda p=path: open(p, "rb"),
            compress=should_compress(name) if compress is None else compress,
        )


@dataclass
class Artifact:
    """A unit of backup output: becomes exactly one zip file."""

    label: str  # item name inside the source, e.g. database name; "" if the source has one item
    entries: list[Entry]
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cleanup: list[Path] = field(default_factory=list)  # local temp files to delete after packaging

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries)


@dataclass
class PackagedFile:
    source_id: int
    source_name: str
    category: str
    topic: str
    label: str
    file_name: str
    size: int
    local_path: Path | None
    archive_rel: str | None = None  # "<date folder>/<file name>" inside the archive
    uploaded: bool = False
    upload_error: str = ""
    entries_count: int = 0
    raw_size: int = 0
    send_telegram: bool = True


@dataclass
class ItemResult:
    source_id: int
    source_name: str
    status: str = OK
    message: str = ""
    files: list[PackagedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0
    artifacts_meta: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


_STORED_EXT = {
    ".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".lz4", ".cab",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".m4a", ".ogg",
    ".docx", ".xlsx", ".pptx", ".pdf", ".apk", ".iso",
}


def should_compress(name: str) -> bool:
    """Skip deflate for formats that are already compressed (saves a lot of CPU)."""
    return os.path.splitext(name.lower())[1] not in _STORED_EXT
