"""Sending zips and reports to the Telegram group over MTProto (up to 2 GB per file)."""

from __future__ import annotations

import asyncio
import html
import logging
import random
from collections.abc import Callable
from pathlib import Path

from telethon import TelegramClient, errors, functions, types

from backuper.db import Store
from backuper.models import CATEGORY_TITLES
from backuper.packager import FileSlice, part_name, part_ranges

log = logging.getLogger(__name__)

PART_SIZE = 512 * 1024          # MTProto maximum upload chunk
BIG_FILE = 10 * 1024 * 1024     # files above this use SaveBigFilePart
MAX_TG_FILE = 2000 * 1024 * 1024
TOPICS_KEY = "telegram_topics"  # {chat_id: {title: topic_id}}

# Errors that mean "that topic no longer exists" (names vary across Telethon layers)
_TOPIC_GONE = tuple(
    e for e in (getattr(errors, n, None) for n in
                ("TopicDeletedError", "MessageIdInvalidError", "ReplyMessageIdInvalidError"))
    if e is not None
)


class TelegramError(RuntimeError):
    pass


class TelegramSender:
    def __init__(self, client: TelegramClient, store: Store, admin_ids: Callable[[], set[int]]):
        self.client = client
        self.store = store
        self.admin_ids = admin_ids
        self._topic_lock = asyncio.Lock()

    # -- chat / topics ----------------------------------------------------------
    async def chat(self):
        raw = str(self.store.get_setting("chat_id") or "").strip()
        if not raw:
            raise TelegramError("No Telegram group set — add the bot to the group as admin and send /bind there")
        try:
            return await self.client.get_input_entity(int(raw))
        except (ValueError, errors.RPCError) as e:
            raise TelegramError(f"Can't reach Telegram group {raw}: {e}") from e

    def topic_title(self, category: str, source_name: str, override: str = "") -> str | None:
        mode = self.store.get_setting("topic_mode") or "category"
        if override:
            return override
        if mode == "none":
            return None
        if mode == "source" and category != "reports":
            return source_name
        return CATEGORY_TITLES.get(category, category.title())

    async def topic_id(self, title: str | None) -> int | None:
        if not title:
            return None
        async with self._topic_lock:
            topics: dict = self.store.get_json(TOPICS_KEY, {}) or {}
            chat_key = str(self.store.get_setting("chat_id"))
            per_chat = topics.get(chat_key, {})
            if title in per_chat:
                return per_chat[title] or None
            tid = await self._create_topic(title)
            per_chat[title] = tid or 0
            topics[chat_key] = per_chat
            self.store.set_json(TOPICS_KEY, topics)
            return tid

    def forget_topic(self, title: str) -> None:
        topics: dict = self.store.get_json(TOPICS_KEY, {}) or {}
        topics.get(str(self.store.get_setting("chat_id")), {}).pop(title, None)
        self.store.set_json(TOPICS_KEY, topics)

    async def _create_topic(self, title: str) -> int | None:
        peer = await self.chat()
        try:
            if hasattr(functions.messages, "CreateForumTopicRequest"):
                req = functions.messages.CreateForumTopicRequest(peer=peer, title=title[:128],
                                                                 random_id=random.getrandbits(63))
            else:  # older Telethon layers
                req = functions.channels.CreateForumTopicRequest(channel=peer, title=title[:128],
                                                                 random_id=random.getrandbits(63))
            result = await self.client(req)
        except errors.RPCError as e:
            log.warning("Can't create topic %r (%s) — posting to the main chat instead. "
                        "Enable Topics in the group and give the bot 'Manage topics'.", title, e)
            return None
        for upd in getattr(result, "updates", []):
            msg = getattr(upd, "message", None)
            if isinstance(msg, types.MessageService) and isinstance(msg.action, types.MessageActionTopicCreate):
                log.info("created Telegram topic %r (id %d)", title, msg.id)
                return msg.id
        for upd in getattr(result, "updates", []):
            if isinstance(upd, types.UpdateMessageID):
                return upd.id
        return None

    # -- sending ----------------------------------------------------------------------
    async def _with_topic(self, title: str | None, send: Callable[[int | None], object]):
        tid = await self.topic_id(title)
        try:
            return await send(tid)
        except _TOPIC_GONE:
            if not title:
                raise
            log.info("Topic %r is gone, recreating it", title)
            self.forget_topic(title)
            return await send(await self.topic_id(title))

    async def send_text(self, text: str, topic: str | None = None):
        """Send a message into a topic; returns the message (so it can be edited later)."""
        peer = await self.chat()

        async def send(tid):
            return await self.client.send_message(peer, text, parse_mode="html", reply_to=tid,
                                                  link_preview=False)
        return await self._with_topic(topic, send)

    async def edit_text(self, message, text: str) -> None:
        """Edit a message sent with send_text (live run progress)."""
        try:
            await self.client.edit_message(message, text, parse_mode="html", link_preview=False)
        except errors.MessageNotModifiedError:
            pass

    async def notify_admins(self, text: str) -> None:
        for uid in self.admin_ids():
            try:
                await self.client.send_message(uid, text, parse_mode="html", link_preview=False)
            except errors.RPCError as e:
                log.warning("Can't DM admin %s: %s (they must /start the bot once)", uid, e)

    async def upload_file(self, path: Path, file_name: str, captions: Callable[[int, int], str],
                          topic: str | None, progress: Callable[[int], None] | None = None,
                          retries: int = 3) -> int:
        """Upload `path`, split into parts if needed. `captions(i, n)` builds each part's caption.
        Returns the number of parts sent."""
        peer = await self.chat()
        size = path.stat().st_size
        split = min(int(self.store.get_setting("split_mb") or 1950) * 1024 * 1024, MAX_TG_FILE)
        ranges = part_ranges(size, split)
        sent_before = 0
        for i, (offset, length) in enumerate(ranges, start=1):
            pname = part_name(file_name, i, len(ranges))
            last_err: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    def prog(done: int, base=sent_before):
                        if progress:
                            progress(base + done)
                    log.debug("uploading part %d/%d %s (%d bytes), attempt %d", i, len(ranges), pname, length, attempt)
                    handle = await fast_upload(self.client, path, offset, length, pname, prog)
                    caption = captions(i, len(ranges))

                    async def send(tid, handle=handle, caption=caption, pname=pname):
                        return await self.client.send_file(
                            peer, handle, caption=caption, parse_mode="html", force_document=True,
                            reply_to=tid, mime_type="application/zip",
                            attributes=[types.DocumentAttributeFilename(pname)],
                        )
                    await self._with_topic(topic, send)
                    last_err = None
                    break
                except (errors.RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
                    last_err = e
                    wait = 15 * attempt
                    log.warning("upload of %s failed (%d/%d): %s — retrying in %ds", pname, attempt, retries, e, wait)
                    if attempt < retries:
                        await asyncio.sleep(wait)
            if last_err is not None:
                raise TelegramError(f"upload of {pname} failed: {last_err}") from last_err
            sent_before += length
        return len(ranges)


async def fast_upload(client: TelegramClient, path: Path, offset: int, length: int, name: str,
                      progress: Callable[[int], None] | None = None, workers: int = 6):
    """Upload a byte range of a file with several parts in flight at once.

    Telethon's built-in upload sends one 512 KB part at a time and waits for
    each acknowledgement, which is slow on high-latency links. Parts may arrive
    in any order, so we pipeline them.
    """
    file_id = random.getrandbits(63)
    total_parts = max(1, (length + PART_SIZE - 1) // PART_SIZE)
    big = length > BIG_FILE
    sem = asyncio.Semaphore(workers)
    done = 0
    failures: list[BaseException] = []

    async def send_part(index: int, data: bytes) -> None:
        nonlocal done
        try:
            for attempt in range(4):
                try:
                    if big:
                        req = functions.upload.SaveBigFilePartRequest(file_id, index, total_parts, data)
                    else:
                        req = functions.upload.SaveFilePartRequest(file_id, index, data)
                    if not await client(req):
                        raise TelegramError(f"Telegram refused part {index}")
                    break
                except (errors.RPCError, ConnectionError, asyncio.TimeoutError):
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
            done += len(data)
            if progress:
                progress(done)
        except BaseException as e:  # noqa: BLE001 - collected and re-raised below
            failures.append(e)
        finally:
            sem.release()

    tasks = []
    with FileSlice(path, offset, length, name) as f:
        for index in range(total_parts):
            if failures:
                break
            data = f.read(PART_SIZE)
            await sem.acquire()
            tasks.append(asyncio.create_task(send_part(index, data)))
    await asyncio.gather(*tasks)
    if failures:
        raise failures[0]
    if big:
        return types.InputFileBig(file_id, total_parts, name)
    return types.InputFile(file_id, total_parts, name, "")


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)
