from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backuper.config import AppConfig
from backuper.crypto import SecretBox
from backuper.db import Store


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    data = tmp_path / "data"
    staging = data / "staging"
    for d in (data, staging, data / "logs"):
        d.mkdir(parents=True, exist_ok=True)
    return AppConfig(bot_token="x", api_id=1, api_hash="x", owner_ids=frozenset({42}),
                     secret_key=SecretBox.generate_key(), timezone=ZoneInfo("Asia/Tehran"),
                     data_dir=data, staging_dir=staging, tg_proxy="", log_level="INFO")


@pytest.fixture
def store(cfg: AppConfig) -> Store:
    s = Store(cfg.db_path, SecretBox(cfg.secret_key))
    yield s
    s.close()
