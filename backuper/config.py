"""Bootstrap configuration read from the environment / .env file.

Only what is needed before the database exists lives here. Everything an
operator changes day to day is stored in the database and edited from the bot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppConfig:
    bot_token: str
    api_id: int
    api_hash: str
    owner_ids: frozenset[int]
    secret_key: str
    timezone: ZoneInfo
    data_dir: Path
    staging_dir: Path
    tg_proxy: str
    log_level: str
    env_file: Path | None = None   # the .env this configuration came from (backed up by the "self" source)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "backuper.db"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "telegram"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as e:
            raise ConfigError(f"OWNER_IDS contains a non-numeric value: {part!r}") from e
    return frozenset(ids)


def load_config(env_file: str | os.PathLike | None = None, *, require_telegram: bool = True) -> AppConfig:
    load_dotenv(env_file or Path.cwd() / ".env", override=False)

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    secret_key = get("SECRET_KEY")
    if not secret_key:
        raise ConfigError("SECRET_KEY is empty. Run `backuper gen-key` and put the value in .env")

    bot_token, api_hash, api_id_raw = get("BOT_TOKEN"), get("API_HASH"), get("API_ID")
    api_id = 0
    if require_telegram:
        missing = [n for n, v in (("BOT_TOKEN", bot_token), ("API_ID", api_id_raw), ("API_HASH", api_hash)) if not v]
        if missing:
            raise ConfigError(f"Missing in .env: {', '.join(missing)}")
    if api_id_raw:
        try:
            api_id = int(api_id_raw)
        except ValueError as e:
            raise ConfigError("API_ID must be a number") from e

    # Relative folders are relative to the .env file, so the CLI works from any current folder.
    base = Path(env_file).expanduser().resolve().parent if env_file else Path.cwd()
    data_dir = (base / Path(get("DATA_DIR", "./data")).expanduser()).resolve()
    staging_raw = get("STAGING_DIR")
    staging_dir = (base / Path(staging_raw).expanduser()).resolve() if staging_raw else data_dir / "staging"

    tz_name = get("TIMEZONE", "Asia/Tehran")
    try:
        tz = ZoneInfo(tz_name)
    except Exception as e:  # noqa: BLE001 - surface a readable error
        raise ConfigError(f"Unknown TIMEZONE {tz_name!r}") from e

    cfg = AppConfig(
        bot_token=bot_token,
        api_id=api_id,
        api_hash=api_hash,
        owner_ids=_parse_ids(get("OWNER_IDS")),
        secret_key=secret_key,
        timezone=tz,
        data_dir=data_dir,
        staging_dir=staging_dir,
        tg_proxy=get("TG_PROXY"),
        log_level=get("LOG_LEVEL", "INFO").upper(),
        env_file=Path(env_file).expanduser().resolve() if env_file else (Path.cwd() / ".env").resolve(),
    )
    for d in (cfg.data_dir, cfg.staging_dir, cfg.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
