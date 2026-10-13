# backuper/util.py

**Purpose:** small, dependency-free helpers used across the project.

**Used by:** [engine.py](engine.md), [bot/panel.py](bot/panel.md), sources, [transport/smb.py](transport/smb.md)
(`match_any`), [destinations](destinations/archive.md).

| Name | What it does / why |
|---|---|
| `_UNSAFE` | Regex of characters not allowed in Windows file names. |
| `human_size` | `1536` → `"1.5 KB"` (binary units). |
| `human_duration` | `3725` → `"1h 02m"`. |
| `safe_filename` | Makes a string safe as a file name on Windows and Linux (removes `\ / : * ? " < > |`, collapses spaces to `-`, keeps Persian letters). Used for zip names and bot-made `.bak` names. |
| `hashtag` | `"Center-Core"` → `#Center_Core` for Telegram captions (searchable). |
| `match_any` | Case-insensitive wildcard match against a list of patterns (exclude lists, file patterns). |
| `short_error` | One-line, length-limited error text with the exception class — what users see in reports. |
