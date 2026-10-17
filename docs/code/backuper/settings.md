# backuper/settings.py

**Purpose:** declares the global settings an admin can change in ⚙️ Settings (stored in the database,
not in `.env`). Each is a [`Field`](fields.md), so the bot renders and validates them automatically and
`backuper set KEY` / `backuper import` use the same rules.

**Used by:** [db.py](db.md) (`get_setting` defaults, secret handling), [bot/panel.py](bot/panel.md)
(settings page), [cli.py](cli.md) (`set`, `import`, `export`), [engine.py](engine.md) (`FOLDER_FORMATS`).

| Name | What it is |
|---|---|
| `FOLDER_FORMATS` | Allowed date-folder styles: `1405-07-15`, `1405_07_15`, `1405.07.15` → strftime format. |
| `SETTINGS_FIELDS` | The settings: archive location, archive credential, archive days to keep, folder format, zip password (secret), compression level, Telegram group (set by `/bind`), topic layout, Telegram part size, upload retries, parallel sources, **cloud copy** (off/drive/local), **cloud sign-in**, **cloud folder**, **cloud days to keep** (14), **retry failed sources after (min)** (60, 0 = off) and **retries of failed sources** (3), DM admins on failure, post report when all OK. |
| `SETTINGS_BY_KEY` | The same, as a dict by key. |

**History:** until v1.2 there was also a global `schedule` setting. It was replaced by backup **groups**
(each with its own time); [db.py](db.md) migrates an old global schedule into a group named "Daily".
