"""Jalali (Persian calendar) date helpers for archive folder names and captions."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import jdatetime

DEFAULT_FOLDER_FORMAT = "%Y-%m-%d"
_FOLDER_RE = re.compile(r"^(\d{4})[-_.](\d{2})[-_.](\d{2})$")


def now_jalali(tz: ZoneInfo) -> jdatetime.datetime:
    return jdatetime.datetime.fromgregorian(datetime=datetime.now(tz))


def to_jalali(dt: datetime) -> jdatetime.datetime:
    return jdatetime.datetime.fromgregorian(datetime=dt)


def folder_name(dt: jdatetime.datetime, fmt: str = DEFAULT_FOLDER_FORMAT) -> str:
    return dt.strftime(fmt)


def stamp(dt: jdatetime.datetime) -> str:
    """Compact sortable timestamp for file names, e.g. 1405-07-15_0300."""
    return dt.strftime("%Y-%m-%d_%H%M")


def pretty(dt: jdatetime.datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M")


def parse_folder(name: str) -> tuple[int, int, int] | None:
    """Return (y, m, d) if `name` looks like a Jalali date folder, else None.

    Archive retention only ever deletes folders this recognises, so anything
    else an operator puts in the archive root is left alone.
    """
    m = _FOLDER_RE.match(name)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if not (1300 <= y <= 1600 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return y, mo, d
