"""SQLite persistence. Small, synchronous and guarded by a lock — the data is tiny."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backuper.crypto import PREFIX, SecretBox
from backuper.models import Credential, Group, SourceRecord
from backuper.settings import SETTINGS_BY_KEY

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    username TEXT NOT NULL DEFAULT '',
    secret TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    jdate TEXT NOT NULL DEFAULT '',
    total_bytes INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_id INTEGER,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    bytes INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS run_items_run ON run_items(run_id);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    schedule TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    added_by INTEGER,
    added_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str, box: SecretBox):
        self.box = box
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        self._migrate_main_schedule()

    def _migrate_main_schedule(self) -> None:
        """v1 had one global 'schedule' setting; turn it into a group called Daily once."""
        if self.get_json("migrated_groups"):
            return
        legacy = self._q("SELECT value FROM settings WHERE key='schedule'")
        rows = self._q("SELECT id, config FROM sources")
        ungrouped = [r for r in rows if not json.loads(r["config"]).get("schedule")
                     and not json.loads(r["config"]).get("group")]
        if ungrouped and not self.list_groups():
            schedule = json.loads(legacy[0]["value"]) if legacy else "0 3 * * *"
            self.save_group("Daily", schedule)
            for r in ungrouped:  # edit the raw JSON: secrets stay encrypted as they are
                cfg = json.loads(r["config"])
                cfg["group"] = "Daily"
                self._x("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg, ensure_ascii=False), r["id"]))
        self.set_json("migrated_groups", True)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _x(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.lastrowid or cur.rowcount

    # -- secrets inside JSON configs ----------------------------------------
    def _encrypt_config(self, config: dict[str, Any], secret_keys: Iterable[str]) -> dict[str, Any]:
        out = dict(config)
        for k in secret_keys:
            v = out.get(k)
            if isinstance(v, str) and v and not v.startswith(PREFIX):
                out[k] = self.box.encrypt(v)
        return out

    def _decrypt_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return {k: self.box.decrypt(v) if isinstance(v, str) and v.startswith(PREFIX) else v
                for k, v in config.items()}

    # -- settings -------------------------------------------------------------
    def get_setting(self, key: str) -> Any:
        rows = self._q("SELECT value FROM settings WHERE key=?", (key,))
        f = SETTINGS_BY_KEY.get(key)
        if not rows:
            return f.default if f else None
        value = json.loads(rows[0]["value"])
        if f is not None and f.secret and isinstance(value, str):
            return self.box.decrypt(value)
        return value

    def set_setting(self, key: str, value: Any) -> None:
        f = SETTINGS_BY_KEY.get(key)
        if f is not None and f.secret and isinstance(value, str):
            value = self.box.encrypt(value)
        self._x(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def all_settings(self) -> dict[str, Any]:
        return {k: self.get_setting(k) for k in SETTINGS_BY_KEY}

    def get_json(self, key: str, default: Any = None) -> Any:
        """Internal (non-user-facing) values, e.g. Telegram topic ids."""
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_json(self, key: str, value: Any) -> None:
        self._x(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # -- credentials ---------------------------------------------------------
    def _cred(self, r: sqlite3.Row) -> Credential:
        return Credential(id=r["id"], name=r["name"], username=r["username"],
                          password=self.box.decrypt(r["secret"]), note=r["note"])

    def list_credentials(self) -> list[Credential]:
        return [self._cred(r) for r in self._q("SELECT * FROM credentials ORDER BY name COLLATE NOCASE")]

    def get_credential(self, cred_id: int) -> Credential | None:
        rows = self._q("SELECT * FROM credentials WHERE id=?", (cred_id,))
        return self._cred(rows[0]) if rows else None

    def get_credential_by_name(self, name: str) -> Credential | None:
        rows = self._q("SELECT * FROM credentials WHERE name=?", (name,))
        return self._cred(rows[0]) if rows else None

    def save_credential(self, name: str, username: str, password: str | None, note: str = "",
                        cred_id: int | None = None) -> int:
        """Create or update. `password=None` keeps the stored password."""
        now = _now()
        if cred_id is None:
            existing = self.get_credential_by_name(name)
            cred_id = existing.id if existing else None
        if cred_id is None:
            return self._x(
                "INSERT INTO credentials(name, username, secret, note, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (name, username, self.box.encrypt(password or ""), note, now, now),
            )
        if password is None:
            self._x("UPDATE credentials SET name=?, username=?, note=?, updated_at=? WHERE id=?",
                    (name, username, note, now, cred_id))
        else:
            self._x("UPDATE credentials SET name=?, username=?, secret=?, note=?, updated_at=? WHERE id=?",
                    (name, username, self.box.encrypt(password), note, now, cred_id))
        return cred_id

    def rename_credential_refs(self, old: str, new: str, credential_keys: dict[str, set[str]]) -> None:
        """Keep sources/settings pointing at a credential after it's renamed."""
        for src in self.list_sources():
            keys = credential_keys.get(src.type, set())
            changed = {k: new for k in keys if src.config.get(k) == old}
            if changed:
                self.update_source(src.id, config={**src.config, **changed})
        if self.get_setting("archive_credential") == old:
            self.set_setting("archive_credential", new)

    def delete_credential(self, cred_id: int) -> None:
        self._x("DELETE FROM credentials WHERE id=?", (cred_id,))

    # -- sources ---------------------------------------------------------------
    def _src(self, r: sqlite3.Row) -> SourceRecord:
        return SourceRecord(id=r["id"], name=r["name"], type=r["type"], enabled=bool(r["enabled"]),
                            config=self._decrypt_config(json.loads(r["config"])),
                            state=json.loads(r["state"]))

    def list_sources(self, enabled_only: bool = False) -> list[SourceRecord]:
        sql = "SELECT * FROM sources" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name COLLATE NOCASE"
        return [self._src(r) for r in self._q(sql)]

    def get_source(self, source_id: int) -> SourceRecord | None:
        rows = self._q("SELECT * FROM sources WHERE id=?", (source_id,))
        return self._src(rows[0]) if rows else None

    def get_source_by_name(self, name: str) -> SourceRecord | None:
        rows = self._q("SELECT * FROM sources WHERE name=?", (name,))
        return self._src(rows[0]) if rows else None

    def create_source(self, name: str, type_id: str, config: dict[str, Any], secret_keys: Iterable[str],
                      enabled: bool = True) -> int:
        now = _now()
        return self._x(
            "INSERT INTO sources(name, type, enabled, config, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (name, type_id, int(enabled), json.dumps(self._encrypt_config(config, secret_keys), ensure_ascii=False),
             "{}", now, now),
        )

    def update_source(self, source_id: int, *, name: str | None = None, config: dict[str, Any] | None = None,
                      secret_keys: Iterable[str] = (), enabled: bool | None = None,
                      type_id: str | None = None) -> None:
        sets, params = ["updated_at=?"], [_now()]
        if name is not None:
            sets.append("name=?")
            params.append(name)
        if type_id is not None:
            sets.append("type=?")
            params.append(type_id)
        if config is not None:
            sets.append("config=?")
            params.append(json.dumps(self._encrypt_config(config, secret_keys), ensure_ascii=False))
        if enabled is not None:
            sets.append("enabled=?")
            params.append(int(enabled))
        params.append(source_id)
        self._x(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", params)

    def set_source_state(self, source_id: int, state: dict[str, Any]) -> None:
        self._x("UPDATE sources SET state=? WHERE id=?", (json.dumps(state, ensure_ascii=False), source_id))

    def delete_source(self, source_id: int) -> None:
        self._x("DELETE FROM sources WHERE id=?", (source_id,))

    # -- groups -----------------------------------------------------------------
    @staticmethod
    def _grp(r: sqlite3.Row) -> Group:
        return Group(id=r["id"], name=r["name"], schedule=r["schedule"], enabled=bool(r["enabled"]))

    def list_groups(self) -> list[Group]:
        return [self._grp(r) for r in self._q("SELECT * FROM groups ORDER BY name COLLATE NOCASE")]

    def get_group(self, group_id: int) -> Group | None:
        rows = self._q("SELECT * FROM groups WHERE id=?", (group_id,))
        return self._grp(rows[0]) if rows else None

    def get_group_by_name(self, name: str) -> Group | None:
        rows = self._q("SELECT * FROM groups WHERE name=?", (name,))
        return self._grp(rows[0]) if rows else None

    def save_group(self, name: str, schedule: str, enabled: bool = True, group_id: int | None = None) -> int:
        now = _now()
        if group_id is None:
            existing = self.get_group_by_name(name)
            group_id = existing.id if existing else None
        if group_id is None:
            return self._x("INSERT INTO groups(name, schedule, enabled, created_at, updated_at) VALUES(?,?,?,?,?)",
                           (name, schedule, int(enabled), now, now))
        old = self.get_group(group_id)
        self._x("UPDATE groups SET name=?, schedule=?, enabled=?, updated_at=? WHERE id=?",
                (name, schedule, int(enabled), now, group_id))
        if old and old.name != name:
            self._set_group_refs(old.name, name)
        return group_id

    def delete_group(self, group_id: int) -> None:
        g = self.get_group(group_id)
        if g:
            self._set_group_refs(g.name, "")
            self._x("DELETE FROM groups WHERE id=?", (group_id,))

    def _set_group_refs(self, old: str, new: str) -> None:
        for r in self._q("SELECT id, config FROM sources"):
            cfg = json.loads(r["config"])
            if (cfg.get("group") or "").lower() == old.lower():
                cfg["group"] = new
                self._x("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg, ensure_ascii=False), r["id"]))

    def set_source_group(self, source_id: int, group_name: str) -> None:
        rows = self._q("SELECT config FROM sources WHERE id=?", (source_id,))
        if rows:
            cfg = json.loads(rows[0]["config"])
            cfg["group"] = group_name
            self._x("UPDATE sources SET config=?, updated_at=? WHERE id=?",
                    (json.dumps(cfg, ensure_ascii=False), _now(), source_id))

    def group_members(self, name: str, enabled_only: bool = False) -> list[SourceRecord]:
        return [s for s in self.list_sources(enabled_only=enabled_only)
                if (s.config.get("group") or "").lower() == name.lower()]

    # -- runs --------------------------------------------------------------------
    def create_run(self, trigger: str, jdate: str) -> int:
        return self._x("INSERT INTO runs(trigger, started_at, jdate) VALUES(?,?,?)", (trigger, _now(), jdate))

    def finish_run(self, run_id: int, status: str, total_bytes: int, summary: str) -> None:
        self._x("UPDATE runs SET finished_at=?, status=?, total_bytes=?, summary=? WHERE id=?",
                (_now(), status, total_bytes, summary, run_id))

    def add_run_item(self, run_id: int, source_id: int | None, source_name: str, status: str, message: str,
                     files: list[dict[str, Any]], nbytes: int, seconds: float) -> int:
        return self._x(
            "INSERT INTO run_items(run_id, source_id, source_name, status, message, files, bytes, seconds) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (run_id, source_id, source_name, status, message, json.dumps(files, ensure_ascii=False), nbytes, seconds),
        )

    def update_run_item_files(self, item_id: int, files: list[dict[str, Any]]) -> None:
        self._x("UPDATE run_items SET files=? WHERE id=?", (json.dumps(files, ensure_ascii=False), item_id))

    def list_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    def run_items(self, run_id: int) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM run_items WHERE run_id=? ORDER BY id", (run_id,))

    def get_run_item(self, item_id: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM run_items WHERE id=?", (item_id,))
        return rows[0] if rows else None

    def last_item_for_source(self, source_id: int) -> sqlite3.Row | None:
        rows = self._q("SELECT ri.*, r.jdate, r.started_at FROM run_items ri JOIN runs r ON r.id = ri.run_id "
                       "WHERE ri.source_id=? ORDER BY ri.id DESC LIMIT 1", (source_id,))
        return rows[0] if rows else None

    def mark_stale_runs(self) -> None:
        """Runs left 'running' by a crash/restart."""
        self._x("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (_now(),))

    def prune_runs(self, keep: int = 500) -> None:
        self._x("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)", (keep,))

    # -- admins --------------------------------------------------------------------
    def list_admins(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM admins ORDER BY added_at")

    def add_admin(self, user_id: int, name: str, added_by: int | None) -> None:
        self._x("INSERT OR REPLACE INTO admins(user_id, name, added_by, added_at) VALUES(?,?,?,?)",
                (user_id, name, added_by, _now()))

    def remove_admin(self, user_id: int) -> None:
        self._x("DELETE FROM admins WHERE user_id=?", (user_id,))

    def admin_ids(self) -> set[int]:
        return {r["user_id"] for r in self._q("SELECT user_id FROM admins")}
