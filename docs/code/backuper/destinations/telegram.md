# backuper/destinations/telegram.py

**Purpose:** everything the engine sends to Telegram: files (≤ 2000 MiB per part over MTProto), the run
report, and DMs to admins — into the bound group, optionally into **forum topics** (per category, per
source, or none) that are created on demand and cached.

**Uses:** Telethon client from [bot/app.py](../bot/app.md), [db.py](../db.md) (settings, topic cache),
[packager.py](../packager.md) (`FileSlice`, part helpers), [models.py](../models.md) (`CATEGORY_TITLES`).
**Used by:** [engine.py](../engine.md), [bot/panel.py](../bot/panel.md) (`/bind` topic creation), [cli.py](../cli.md) `run`.

## Key behaviours and why

- **Fast pipelined upload (`fast_upload`).** Telethon's built-in upload sends one 512 KB part and waits
  for its acknowledgement before sending the next — slow on high-latency links. Parts may arrive in any
  order, so we keep 6 in flight. Each part is retried 4×; a whole file 3× (setting).
- **Big files** are uploaded as byte ranges of the zip (`name.zip.001`, `.002`…) — no extra disk space.
- **Topics:** created with `messages.CreateForumTopicRequest` (older Telethon layers: `channels.`); if the
  group has no topics or the bot can't create them, it posts to the main chat. A deleted topic is
  recreated automatically.

## Classes and functions

| Name | What it does |
|---|---|
| `PART_SIZE`, `BIG_FILE`, `MAX_TG_FILE`, `TOPICS_KEY`, `_TOPIC_GONE` | Upload chunk (512 KB), "big file" threshold (10 MB), Telegram's max part, topic-cache key, errors meaning "topic is gone". |
| `TelegramError` | Telegram not configured / upload failed after retries. |
| `TelegramSender` | The sender used by the engine. |
| `TelegramSender.__init__` | Client, store, function returning admin ids. |
| `TelegramSender.chat` | The bound group (`chat_id` setting, set by `/bind`). |
| `TelegramSender.topic_title` | Which topic a source posts to (override → per source → per category → none). |
| `TelegramSender.topic_id` | Topic id for a title, creating and caching it per chat. |
| `TelegramSender.forget_topic` | Drop a cached topic (it was deleted). |
| `TelegramSender._create_topic` | Creates a forum topic; returns its id or `None` (then main chat). |
| `TelegramSender._with_topic` | Sends into a topic; recreates it once if it was deleted. |
| `TelegramSender.send_text` | Report/text message into a topic; returns the message so it can be edited. |
| `TelegramSender.edit_text` | Edits such a message (the live progress message of scheduled runs). |
| `TelegramSender.notify_admins` | DM every admin (they must have pressed /start once). |
| `TelegramSender.upload_file` | Uploads a zip, split into parts if needed, with a caption per part. |
| `fast_upload` | The pipelined MTProto upload of one byte range (see above). |
| `esc` | HTML-escape text for Telegram's HTML parse mode. |
