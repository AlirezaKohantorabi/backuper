# backuper/bot/app.py

**Purpose:** builds the Telegram side of the service and wires the parts together: one Telethon client
(logged in **as the bot** with `BOT_TOKEN`, over MTProto using `API_ID`/`API_HASH`), the
[TelegramSender](../destinations/telegram.md), the [Engine](../engine.md), the
[Scheduler](../scheduler.md) and the [Panel](panel.md).

**Why Telethon/MTProto instead of the plain Bot API?** The Bot API limits uploads to 50 MB; SQL backups
are gigabytes. MTProto allows 2000 MiB per file, so a 11 GB backup is 6 parts instead of ~230. The same
client also receives the admin panel's messages, so there is only one connection.

**Used by:** [cli.py](../cli.md) `serve` (and `run`, which reuses `make_client`).

| Name | What it does |
|---|---|
| `COMMANDS` | Commands registered in Telegram's "/" menu. |
| `proxy_kwargs` | `TG_PROXY` → Telethon arguments (SOCKS5/HTTP via python-socks, or MTProxy). |
| `make_client` | Creates the Telethon client (session file in `DATA_DIR`, auto-reconnect, long flood-wait tolerance). |
| `BotApp` | The running bot. |
| `BotApp.__init__` | Creates client, sender, engine, scheduler, panel. |
| `BotApp.admin_ids` | Owners from `.env` ∪ admins added in the bot. |
| `BotApp.is_admin` | Gate for every panel action. |
| `BotApp._restart_when_requested` | Waits for a restart request ([control](../control.md)), then disconnects — the process ends and systemd starts it again. |
| `BotApp._say_back_online` | After such a restart: "✅ Back online — Backuper x.y.z" to whoever asked. |
| `BotApp.run` | Logs in, sets the command menu, cleans up after a crash, registers handlers, starts the scheduler and the [status file](../status.md) writer, runs until disconnected. |
