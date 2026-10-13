# Setup guide

Takes ~20 minutes for a new site. You need: a Linux server (Ubuntu 22.04+/Debian 12+)
that can reach the things to back up, and a Telegram account.

## 1. Telegram

1. **Bot** — talk to [@BotFather](https://t.me/BotFather): `/newbot` → pick a name and
   username → copy the **token**. Optional: `/setprivacy` → *Enable* (default).
2. **API ID/hash** — log in at <https://my.telegram.org> → *API development tools* →
   create an app (any name) → copy **api_id** and **api_hash**. The bot uses these to
   upload over MTProto (2 GB per file instead of the Bot API's 50 MB). They're tied to
   your account but the bot only ever logs in *as the bot*.
3. **Your user ID** — send `/start` to [@userinfobot](https://t.me/userinfobot), or later
   send `/id` to your bot.
4. **Group** — create a group, *Edit → Topics → on* (recommended), add the bot and make it
   **admin** with *Post messages*, *Delete messages*, *Manage topics*.

## 2. Server

```bash
git clone https://github.com/<you>/backuper.git ~/backuper-app
cd ~/backuper-app
./install.sh
nano .env          # BOT_TOKEN, API_ID, API_HASH, OWNER_IDS (comma-separated user IDs)
sudo systemctl restart backuper
journalctl -u backuper -f
```

- Without `BOT_TOKEN`/`API_ID`/`API_HASH` the service runs **archive-only**: schedules run and
  zips go to the archive, but nothing is uploaded and there's no admin panel (manage it with the
  CLI: `backuper import`, `credential`, `set`, `test`, `list`). Fill `.env` + restart to enable Telegram.
- Set the server clock right (`timedatectl`): schedules use `TIMEZONE` from `.env`
  (default `Asia/Tehran`) and the archive folders are Jalali dates.
- If Telegram is only reachable through a proxy, set `TG_PROXY` in `.env`.
- `STAGING_DIR` needs free space for one day's zips (they're deleted after upload).

## 3. In the bot

1. Send `/menu` to the bot.
2. In the group send **`/bind`** — the bot creates the topics
   (🗄 Databases, 🌐 Network, 🌍 Websites, 📁 File Servers, 📊 Reports).
3. **⚙️ Settings**
   - *Zip password* — set it, and keep a copy somewhere safe outside Telegram.
   - *Archive location* — e.g. `\\192.168.10.35\Backuper`, *Archive credential* — a Windows
     account with write access. *Archive days to keep* — 14.
   - *🧪 Test archive*.
4. **🔑 Credentials** — add the logins (Windows/domain account for shares, SQL login,
   router user, panel logins). Or from the server's terminal, typed hidden:
   `.venv/bin/backuper credential <name> -u <username>`. For Windows shares, run
   [windows/setup-backuper-access.ps1](windows/setup-backuper-access.ps1) on each server first.
5. **📦 Sources → ➕ Add source** — for each server. Use **🧪 Test** on each one.
6. **🗂 Groups** — create a group (e.g. *Daily*, 08:00) and tick its sources. Each run uploads
   smallest file first, one at a time. Sources outside groups can have their own time.
7. **▶️ Run all now** to try the whole thing once.

Bulk alternative to steps 4–5: write a YAML file (see
[examples/sources.example.yaml](../examples/sources.example.yaml)), then
`.venv/bin/backuper import file.yaml`, set the passwords in the bot (🔑 Credentials) and
`sudo systemctl restart backuper`.

## Restoring

- **Zip**: open with 7-Zip or WinRAR and enter the zip password. Split uploads: download all
  `.zip.001`, `.zip.002`… into one folder, open the `.001`.
- **SQL**: `RESTORE DATABASE … FROM DISK = '…bak' WITH MOVE …` (or SSMS → Restore).
- **MikroTik**: `.backup` → *Files* upload + `/system backup load` (same model only).
  `.rsc` → `/import file-name=…` (portable, review it first).
- **cPanel**: WHM → *Transfer or Restore a cPanel Account*, or extract `homedir/` and the
  `mysql/*.sql` dumps by hand. **DirectAdmin**: *Create/Restore Backups* (put the file in
  `~/backups/`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing happens on `/menu` | your ID isn't in `OWNER_IDS` (send `/id`), restart after editing `.env` |
| `No Telegram group set` | send `/bind` in the group |
| Topics not created | enable Topics in the group, give the bot *Manage topics*, ⚙️ → *Reset topics* |
| `SSH host key changed` | device replaced/reinstalled → source → *Forget pinned SSH key* |
| `newest backup … is 40h old` | the SQL job on that server stopped working |
| SMB `STATUS_LOGON_FAILURE` | wrong user/password; domain users as `DOMAIN\user` |
| SMB `STATUS_ACCESS_DENIED` | share + NTFS permissions (read, plus delete if *keep last* is used) |
| Upload slow / timeouts | check the route to Telegram; consider `TG_PROXY` |
