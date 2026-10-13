# tests/test_jalali.py

| Name | Proves |
|---|---|
| `TZ`, `NOW` | Fixed timezone and "now" (1405/07/21 16:00) for repeatable tests. |
| `test_parse_when_variants` | `18:30` (today), `09:00` (tomorrow), `+90m`, `+2h`, a Jalali date and a Gregorian date. |
| `test_parse_when_rejects_bad_and_past` | Words, invalid times/dates and past times are refused. |
| `test_folder_names` | Date folders are recognised; other names are not (retention safety). |
