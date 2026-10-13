"""Jalali (Persian calendar) date helpers for archive folder names and captions."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
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


_WHEN_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_WHEN_DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T]+(\d{1,2}):(\d{2})$")
_WHEN_REL = re.compile(r"^\+\s*(\d+)\s*([mh])$", re.IGNORECASE)


def parse_when(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """When to run a one-time backup. Accepts:

    * "14:30"            -> the next time it's 14:30 (today, or tomorrow if already past)
    * "1405/07/25 22:00" -> a Jalali date and time (years below 1700 are Jalali)
    * "2026-10-17 22:00" -> a Gregorian date and time
    * "+30m" / "+2h"     -> relative to now
    Raises ValueError with a readable message.
    """
    now = now or datetime.now(tz)
    s = " ".join(text.strip().split())
    if m := _WHEN_REL.match(s):
        n, unit = int(m.group(1)), m.group(2).lower()
        when = now + (timedelta(minutes=n) if unit == "m" else timedelta(hours=n))
    elif m := _WHEN_TIME.match(s):
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise ValueError(f"'{s}' is not a valid time")
        when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
    elif m := _WHEN_DATE.match(s):
        y, mo, d, h, mi = (int(x) for x in m.groups())
        try:
            if y < 1700:
                g = jdatetime.datetime(y, mo, d, h, mi).togregorian()
                when = datetime(g.year, g.month, g.day, g.hour, g.minute, tzinfo=tz)
            else:
                when = datetime(y, mo, d, h, mi, tzinfo=tz)
        except ValueError as e:
            raise ValueError(f"'{s}' is not a valid date/time ({e})") from e
    else:
        raise ValueError("use 14:30, 1405/07/25 22:00 (Jalali), 2026-10-17 22:00 or +2h")
    if when <= now:
        raise ValueError(f"{pretty(to_jalali(when))} is in the past")
    return when


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
