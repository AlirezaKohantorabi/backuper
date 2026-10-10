from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

from telethon import TelegramClient, functions, types
from telethon.network import connection

from backuper.config import AppConfig, ConfigError
from backuper.db import Store
from backuper.destinations.telegram import TelegramSender
from backuper.engine import Engine
from backuper.scheduler import Scheduler

log = logging.getLogger(__name__)

COMMANDS = [
    ("menu", "Admin panel"),
    ("run", "Run all backups now (or /run Name)"),
    ("status", "Progress of the current run"),
    ("cancel", "Cancel the current form"),
    ("id", "Show your Telegram ID / chat ID"),
    ("help", "How it works"),
]


def proxy_kwargs(url: str) -> dict:
    """TG_PROXY=socks5://user:pass@host:port | http://host:port | mtproxy://secret@host:port"""
    if not url:
        return {}
    u = urlparse(url)
    if not u.hostname or not u.port:
        raise ConfigError(f"TG_PROXY must include host and port: {url!r}")
    scheme = u.scheme.lower()
    if scheme in ("socks5", "socks4", "http"):
        proxy = {"proxy_type": scheme, "addr": u.hostname, "port": u.port, "rdns": True}
        if u.username:
            proxy.update(username=unquote(u.username), password=unquote(u.password or ""))
        return {"proxy": proxy}
    if scheme == "mtproxy":
        return {"connection": connection.ConnectionTcpMTProxyRandomizedIntermediate,
                "proxy": (u.hostname, u.port, unquote(u.username or ""))}
    raise ConfigError(f"Unsupported TG_PROXY scheme {scheme!r}")


def make_client(cfg: AppConfig) -> TelegramClient:
    return TelegramClient(
        str(cfg.session_path), cfg.api_id, cfg.api_hash,
        flood_sleep_threshold=300, connection_retries=-1, retry_delay=5, auto_reconnect=True,
        device_model="Backuper", app_version="1.0", **proxy_kwargs(cfg.tg_proxy),
    )


class BotApp:
    def __init__(self, cfg: AppConfig, store: Store):
        from backuper.bot.panel import Panel  # avoid import cycle

        self.cfg = cfg
        self.store = store
        self.client = make_client(cfg)
        self.sender = TelegramSender(self.client, store, self.admin_ids)
        self.engine = Engine(cfg, store, self.sender)
        self.scheduler = Scheduler(self.engine, store, cfg.timezone)
        self.panel = Panel(self)

    def admin_ids(self) -> set[int]:
        return set(self.cfg.owner_ids) | self.store.admin_ids()

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids()

    async def run(self) -> None:
        if not self.cfg.owner_ids:
            log.warning("OWNER_IDS is empty — nobody can use the admin panel. Send /id to the bot and add yours.")
        await self.client.start(bot_token=self.cfg.bot_token)
        me = await self.client.get_me()
        log.info("Logged in as @%s", me.username)
        try:
            await self.client(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(), lang_code="",
                commands=[types.BotCommand(c, d) for c, d in COMMANDS]))
        except Exception:  # noqa: BLE001
            log.debug("couldn't set bot commands", exc_info=True)
        self.engine.startup_cleanup()
        self.panel.register()
        self.scheduler.start()
        try:
            await self.client.run_until_disconnected()
        finally:
            self.scheduler.shutdown()
