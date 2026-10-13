from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backuper import jalali

TZ = ZoneInfo("Asia/Tehran")
NOW = datetime(2026, 10, 13, 16, 0, tzinfo=TZ)   # = 1405/07/21 16:00


def test_parse_when_variants():
    assert jalali.parse_when("18:30", TZ, NOW) == NOW.replace(hour=18, minute=30)
    assert jalali.parse_when("09:00", TZ, NOW) == (NOW + timedelta(days=1)).replace(hour=9, minute=0)  # tomorrow
    assert jalali.parse_when("+90m", TZ, NOW) == NOW + timedelta(minutes=90)
    assert jalali.parse_when("+2h", TZ, NOW) == NOW + timedelta(hours=2)
    assert jalali.parse_when("1405/07/25 22:00", TZ, NOW) == datetime(2026, 10, 17, 22, 0, tzinfo=TZ)
    assert jalali.parse_when("2026-10-17 22:00", TZ, NOW) == datetime(2026, 10, 17, 22, 0, tzinfo=TZ)


def test_parse_when_rejects_bad_and_past():
    for bad in ("tomorrow", "25:00", "1405/13/01 10:00", "1405/07/20 10:00"):
        with pytest.raises(ValueError):
            jalali.parse_when(bad, TZ, NOW)


def test_folder_names():
    assert jalali.parse_folder("1405-07-21") == (1405, 7, 21)
    assert jalali.parse_folder("keep-me") is None
