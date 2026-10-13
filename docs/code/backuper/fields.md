# backuper/fields.py

**Purpose:** a tiny declarative schema for user input. A `Field` describes one setting (label, kind,
default, help, validation, when to show it). Source types, credentials, groups and settings are all
described with `Field`s, and two very different front ends use the same declarations:

- the **Telegram admin panel** ([bot/forms.py](bot/forms.md)) asks one field at a time, shows buttons
  for yes/no, choices, credentials and groups, and validates typed answers with `Field.parse`;
- the **YAML importer** ([cli.py](cli.md) `import`) validates whole config files with `parse_values`.

So adding a new source type only means listing its fields — no bot code.

**Used by:** [sources/*](sources/base.md), [settings.py](settings.md), [bot/panel.py](bot/panel.md),
[bot/forms.py](bot/forms.md), [cli.py](cli.md), [scheduler.py](scheduler.md) (`describe_schedule`),
[archive.py](destinations/archive.md) (`unc_server`).

## Field kinds (constants)

`TEXT`, `INT`, `BOOL`, `CHOICE`, `LIST`, `SECRET`, `CREDENTIAL` (name of a stored credential),
`SCHEDULE` (times / cron lines), `UNC` (network path), `HOST`, `GROUP` (name of a backup group),
`TIME` ("HH:MM").

## Classes and functions

| Name | What it does / why |
|---|---|
| `FieldError` | Raised for invalid input; its message is shown to the user as-is. |
| `Field` | Frozen dataclass: `key`, `label`, `kind`, `required`, `default`, `help`, `example`, `choices`, `min`/`max`, `show_if` (only ask when another field has a given value — e.g. ask "Own time" only when "group" is empty), `tags` (`advanced` = not asked in the add wizard, editable later). |
| `Field.secret` | True for `SECRET` fields → stored encrypted, typed messages deleted from the chat, shown as ••••••. |
| `Field.visible` | Evaluates `show_if` against the current values (a tuple of allowed values is supported). |
| `Field.parse` | Turns raw input (Telegram text or a YAML value) into the stored value; enforces required/min/max/choices; normalises network paths, schedules and times. Empty or "-" means "use the default". |
| `Field.display` | Human-readable value for menus (secrets masked, lists joined, schedules described). |
| `Field.picks_from_list` | True for `CREDENTIAL`/`GROUP` — the bot answers these with buttons of stored objects. |
| `parse_values` | Validate a whole dict against a list of fields (unknown keys are an error). `partial=True` only checks the keys present (used to update an existing source from YAML). |
| `parse_schedule` | Accepts `03:00`, `03:00, 15:30` or cron lines separated by `;`; returns canonical cron lines joined with `;`. Validates cron with APScheduler. |
| `describe_schedule` | Cron → "daily 08:00" (or the cron line itself if it isn't a simple daily time). |
| `normalize_unc` | `//srv/share/dir/` or `\\srv\share\dir\` → `\\srv\share\dir`; requires at least server + share. |
| `unc_server` | The server part of a UNC path (`\\srv\share` → `srv`). |

**Gotcha learned the hard way:** defaults must be stored in the *stored* format. The first version had
the schedule default as `"03:00"` (display format) instead of `"0 3 * * *"`, and the scheduler silently
ignored it. Tests now cover defaults.
