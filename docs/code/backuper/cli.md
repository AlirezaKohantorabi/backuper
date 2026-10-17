# backuper/cli.py

**Purpose:** the `backuper` command. The systemd service runs `backuper serve`; everything else is for
administrators on the server (setup, bulk import, tests, entering passwords without using the chat).

**Uses:** [config](config.md), [db](db.md), [crypto](crypto.md), [fields](fields.md),
[settings](settings.md), [engine](engine.md), [scheduler](scheduler.md), [bot/app](bot/app.md),
[sources](sources/__init__.md), [destinations/archive](destinations/archive.md), [jalali](jalali.md).

## Commands

| Command | What it does |
|---|---|
| `backuper serve` | Bot + scheduler. Without `BOT_TOKEN`/`API_ID`/`API_HASH` → **archive-only mode** (schedules still run, no uploads, no panel). |
| `backuper run [Name…]` | One run now (all enabled sources, or the named ones). Uploads if Telegram is configured, else archive-only. |
| `backuper test [Name…]` | Each source's connection test (no backup is made). |
| `backuper check` | Config, staging space, timezone/Jalali date, owners, zip password, archive write test. |
| `backuper list` | Sources with type and summary. |
| `backuper log [RUN] [--tail N]` | A run's detailed log (default: the newest run). |
| `backuper status` | What's running right now (live progress per source), the last and the next runs — the same text as `E:\Backuper\_backuper-status.txt`. |
| `backuper import FILE.yaml [--dry-run]` | Bulk-load settings, groups, credentials and sources (upsert by name). |
| `backuper export [FILE]` | Current configuration as YAML, **without** passwords. |
| `backuper credential NAME [-u USER]` | Create/update a credential; the password is typed hidden. |
| `backuper set KEY [VALUE]` | Change a setting; secrets are typed hidden. |
| `backuper verify [DATE] [ZIP…] [--all]` | Unzip archived zips again (in memory) and check them with the zip's own AES authentication / CRC (default: the newest date folder). Exit code 1 if anything is damaged. |
| `backuper once WHEN NAME…` / `--list` / `--cancel ID` | One-time run of sources or groups at a time (`14:30`, `'1405/07/25 22:00'`, `+2h`); the running service picks it up within 30 s. Replaces the bot's ⏱ Run once. |
| `backuper restart [--now]` | Restart the service — once no backup is running, or `--now`. No sudo (see [control](control.md)). |
| `backuper gen-key` | Print a new `SECRET_KEY`. |

## Functions

| Name | What it does |
|---|---|
| `log` | The application's root logger name (`backuper`). |
| `setup_logging` | (optional quieter console level for interactive commands) Console (journald) + rotating file `data/logs/backuper.log` (10 MB × 5) at `LOG_LEVEL`, every line tagged `[run#N source]` (see [logctx](logctx.md)); the root logger passes DEBUG so per-run logs get detail; quiets noisy libraries. |
| `open_store` | Opens the database with the `SECRET_KEY` box. |
| `cmd_gen_key` | `gen-key`. |
| `cmd_serve` | `serve` — chooses bot mode or archive-only mode. |
| `_serve_archive_only` | Archive-only mode: engine without a sender + scheduler + the [status file](status.md) writer + leftover clean-up, until a restart is requested. |
| `cmd_run` | `run` — connects Telegram only if configured. |
| `cmd_test` | `test` — calls each plugin's `test()` with a 3-minute limit. |
| `cmd_check` | `check`. |
| `cmd_import` | `import` — validates with the same `Field` rules as the bot; checks that referenced credentials and groups exist; a source whose type changed is updated in place (type included). Passwords left empty in YAML keep the stored ones. |
| `cmd_export` | `export` — settings (no secrets, no chat id), groups, credentials (no passwords), sources (secret fields blanked). |
| `cmd_credential` | `credential` — `getpass` for the password. |
| `cmd_set` | `set`. |
| `cmd_list` | `list`. |
| `cmd_log` | `log` — prints `data/logs/runs/run-<id>.log`. |
| `cmd_status` | `status` — prints `data/status.txt` and warns if it's old (service not running). |
| `cmd_verify` | `verify`. |
| `cmd_once` | `once` — names may be sources or groups (a group = its enabled members). |
| `cmd_restart` | `restart` — writes the request, then reports "restarting", "waiting for backups" or "service not running". |
| `default_env` | Which `.env` to use: `--env`, `$BACKUPER_ENV`, `./.env`, else the installation's own — so `backuper status` works from any folder. |
| `main` | Argument parsing; prints `ConfigError` nicely and exits 2. |
