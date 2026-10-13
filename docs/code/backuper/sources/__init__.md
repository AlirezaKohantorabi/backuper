# backuper/sources/\_\_init\_\_.py

**Purpose:** the **registry** of source types: maps a type id (stored in each source's `type` column)
to its class. The order of `REGISTRY` is the order of the "➕ Add source" menu.

**Used by:** [engine.py](../engine.md) (`get_type`), [bot/panel.py](../bot/panel.md) (menus, forms),
[cli.py](../cli.md) (import/export/test), [db.py](../db.md) callers (`credential_keys_by_type` for renames).

| Name | What it does |
|---|---|
| `REGISTRY` | `{type_id: class}` for: `mssql_collect`, `mssql_trigger`, `mikrotik`, `smb_folder`, `remote_folder`, `cpanel`, `directadmin`. |
| `get_type` | Class for a type id; raises a readable `SourceError` for unknown types. |
| `credential_keys_by_type` | Which config keys of each type hold credential names (so renaming a credential can update every source). |

**To add a source type:** write `backuper/sources/<x>.py` with a `SourceType` subclass (see
[base.md](base.md)), import it here and add it to `REGISTRY`. The bot, import/export and tests pick it up.
