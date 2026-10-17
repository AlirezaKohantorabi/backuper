# backuper/sources/base.py

**Purpose:** the contract every backup source follows, and the context a source gets during a run.

**A source type** declares *what it needs* (`fields`) and implements *how to get the data*
(`collect`). It returns [`Artifact`](../models.md)s — each becomes one zip (or one plain copy). The
engine does everything else: zipping, encryption, archive, Telegram, retention scheduling, reporting.

**Used by:** every file in `sources/`, [engine.py](../engine.md), [bot/panel.py](../bot/panel.md), [cli.py](../cli.md).

## Fields every source has (`COMMON_FIELDS`)

| Key | Meaning |
|---|---|
| `group` | Backup group it belongs to (empty = none). Asked when adding. |
| `schedule` | "Own time" — only shown when there's no group. Empty = manual only. |
| `telegram` | Send to Telegram (asked when adding; hidden for plain copies). |
| `archive_keep` | Keep only the newest N archive copies of this source (advanced). |
| `topic` | Override the Telegram topic (advanced). |

## Classes and functions

| Name | What it does / why |
|---|---|
| `SourceError` | Expected, explainable failure — shown to the user without a traceback. |
| `COMMON_FIELDS` | See table above. |
| `RunContext` | What a plugin gets during a run. |
| `RunContext.__init__` | Store, the source record, a private work folder in staging, the run's Jalali time, a logger, and `app` — the service's configuration (used by the [self backup](self_backup.md)). |
| `RunContext.stamp` | `1405-07-21_0800` — for file names made during this run. |
| `RunContext.credential` | Look up a credential by name (clear error if it was deleted). |
| `RunContext.smb` | An SMB client for a credential name. |
| `RunContext.warn` | Add a warning (turns ✅ into ⚠️). |
| `RunContext.note` | Add an informational note (e.g. "no new backup since …"); if a source produced nothing but notes, the run shows ⏭ instead of a warning. |
| `RunContext.get_state` / `RunContext.set_state` | The source's persistent memory (pinned SSH keys, last delivered file, archive history). |
| `SourceType` | Base class. Class attributes: `type_id`, `title`, `category` (databases/network/websites/files → default topic), `description`, `fields`. |
| `SourceType.__init__` | Keeps the record, its config and the context. |
| `SourceType.all_fields` | Type fields + `COMMON_FIELDS`. |
| `SourceType.secret_keys` | Keys stored encrypted. |
| `SourceType.credential_keys` | Keys that reference credentials. |
| `RunContext.reset_connections` / `RunContext.close` | Every SMB client made by `RunContext.smb` is remembered; the engine closes them when the source is done (and resets them before a retry). |
| `SourceType.describe` | One-line summary for lists (`host`, `share` or `path` by default). |
| `SourceType.collect` | **Required.** Fetch/prepare the data; return Artifacts. |
| `SourceType.test` | Optional connectivity check without making a backup (🧪 Test, `backuper test`). |
| `SourceType.after_success` | Optional. Called only with artifacts that were safely stored; used for "keep last N" on the source side and similar clean-up. Returns notes for the report. |
