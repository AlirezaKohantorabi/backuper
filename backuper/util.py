from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")

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


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 5.0,
    what: str = "operation",
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as e:  # noqa: PERF203
            last = e
            if i == attempts:
                break
            delay = base_delay * (2 ** (i - 1))
            log.warning("%s failed (attempt %d/%d): %s - retrying in %.0fs", what, i, attempts, e, delay)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


class Throttle:
    """Lets a call through at most once every `interval` seconds."""

    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def ready(self, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self._last >= self.interval:
            self._last = now
            return True
        return False


def short_error(e: BaseException, limit: int = 300) -> str:
    text = str(e).strip() or e.__class__.__name__
    text = text.replace("\n", " ")
    if e.__class__.__name__ not in text:
        text = f"{e.__class__.__name__}: {text}"
    return text if len(text) <= limit else text[: limit - 1] + "…"
