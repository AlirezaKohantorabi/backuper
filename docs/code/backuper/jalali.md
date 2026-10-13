# backuper/jalali.py

**Purpose:** everything date-related that users see is in the Persian (Jalali) calendar: archive
folder names (`1405-07-21`), file name stamps (`1405-07-21_0800`), captions and reports. This module
wraps `jdatetime` and also parses the "when" of one-time runs.

**Used by:** [engine.py](engine.md) (folder names, stamps, captions), [sources/base.py](sources/base.md)
(`RunContext.stamp`), [destinations/archive.py](destinations/archive.md) (`parse_folder` for retention),
[bot/panel.py](bot/panel.md) (display, `parse_when`), [cli.py](cli.md) (`check`).

| Name | What it does / why |
|---|---|
| `DEFAULT_FOLDER_FORMAT` | `%Y-%m-%d` → `1405-07-21` (sortable). Other separators can be chosen in ⚙️ Settings. |
| `_FOLDER_RE` | Recognises date-folder names. |
| `now_jalali` | Current time as a Jalali datetime in the configured timezone. |
| `to_jalali` | Gregorian datetime → Jalali datetime. |
| `folder_name` | Jalali date → archive folder name. |
| `stamp` | `1405-07-21_0800` — used in zip names and bot-made `.bak` names (sortable, unique per minute). |
| `pretty` | `1405/07/21 08:00` for humans. |
| `_WHEN_TIME`, `_WHEN_DATE`, `_WHEN_REL` | Patterns for `parse_when`. |
| `parse_when` | One-time run times: `14:30` (next occurrence), `1405/07/25 22:00` (Jalali — years below 1700), `2026-10-17 22:00` (Gregorian), `+30m` / `+2h`. Rejects invalid and past times with a readable message. |
| `parse_folder` | `"1405-07-21"` → `(1405, 7, 21)`, anything else → `None`. **Safety:** archive retention only ever deletes folders this recognises, so other folders in the archive root are never touched. |
