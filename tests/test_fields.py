import pytest

from backuper.fields import BOOL, INT, LIST, Field, FieldError, describe_schedule, normalize_unc, parse_schedule


def test_schedule_times_and_cron():
    assert parse_schedule("03:00") == "0 3 * * *"
    assert parse_schedule("03:00, 15:30") == "0 3 * * *;30 15 * * *"
    assert parse_schedule("0 4 * * 5") == "0 4 * * 5"
    assert parse_schedule("0 4 * * 5; 03:00") == "0 4 * * 5;0 3 * * *"
    assert describe_schedule("0 3 * * *;30 15 * * *") == "daily 03:00, daily 15:30"
    with pytest.raises(FieldError):
        parse_schedule("25:00")
    with pytest.raises(FieldError):
        parse_schedule("every day")


def test_unc_normalization():
    assert normalize_unc("//192.168.10.35/Backuper/") == r"\\192.168.10.35\Backuper"
    assert normalize_unc(r"\\srv\D$\Edari\\") == r"\\srv\D$\Edari"
    with pytest.raises(FieldError):
        normalize_unc(r"\\srv")
    with pytest.raises(FieldError):
        normalize_unc("D:\\Edari")


def test_basic_kinds():
    assert Field("x", "X", INT, min=1).parse("7") == 7
    with pytest.raises(FieldError):
        Field("x", "X", INT, min=1).parse("0")
    assert Field("b", "B", BOOL).parse("yes") is True
    assert Field("b", "B", BOOL).parse("no") is False
    assert Field("l", "L", LIST).parse("a.bak, b.bak\nc.bak") == ["a.bak", "b.bak", "c.bak"]
    assert Field("o", "O", required=False, default="d").parse("") == "d"
    with pytest.raises(FieldError):
        Field("r", "R").parse("  ")
