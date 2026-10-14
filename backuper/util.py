from __future__ import annotations

import fnmatch
import io
import re
from typing import BinaryIO

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a string safe as a Windows/Linux file name (keeps Persian letters)."""
    cleaned = _UNSAFE.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", "-", cleaned)
    return (cleaned or "backup")[:max_len]


def hashtag(name: str) -> str:
    tag = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_")
    return f"#{tag}" if tag else ""


def match_any(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in patterns)


def short_error(e: BaseException, limit: int = 300) -> str:
    text = str(e).strip() or e.__class__.__name__
    text = text.replace("\n", " ")
    if e.__class__.__name__ not in text:
        text = f"{e.__class__.__name__}: {text}"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class IncompleteCopy(OSError):
    """Fewer bytes arrived than the source file has — never accept such a copy."""


def check_complete(stream: BinaryIO, done: int, what: str) -> None:
    """Raise IncompleteCopy if fewer bytes were read than the stream's file holds.
    Streams that can't tell their size (FTP, pipes) are trusted."""
    try:
        end = stream.seek(0, io.SEEK_END)
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(end, int) and done < end:
        raise IncompleteCopy(f"incomplete copy of {what}: read {done} of {end} bytes")
