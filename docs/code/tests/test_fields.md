# tests/test_fields.py

| Test | Proves |
|---|---|
| `test_schedule_times_and_cron` | `03:00`, `03:00, 15:30` and cron lines become canonical cron; descriptions read "daily 03:00"; invalid times are rejected. |
| `test_unc_normalization` | Network paths are normalised (`//srv/share/` → `\\srv\share`); paths without a share or drive paths are rejected. |
| `test_basic_kinds` | int min/max, yes/no, lists, defaults and required fields. |
