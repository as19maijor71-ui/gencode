import json
import logging
import sqlite3
from typing import Optional

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

logger = logging.getLogger(__name__)


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str, fsm_ttl: int = 86400, admin_id: int = 0) -> None:
        self.db_path = db_path
        self.fsm_ttl = fsm_ttl
        self.admin_id = admin_id
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _key_str(self, key: StorageKey) -> str:
        thread = f":{key.thread_id}" if key.thread_id is not None else ""
        return f"{key.chat_id}:{key.user_id}{thread}"

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fsm_states ("
            "key TEXT PRIMARY KEY, "
            "state TEXT, "
            "data TEXT, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS copy_cache ("
            "key TEXT PRIMARY KEY, "
            "text TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS generation_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "username TEXT, "
            "category TEXT, "
            "has_competitor INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS whitelist ("
            "user_id INTEGER PRIMARY KEY, "
            "username TEXT, "
            "approved_by INTEGER, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            f"DELETE FROM fsm_states WHERE updated_at < datetime('now', '-{self.fsm_ttl} seconds')"
        )
        conn.execute(
            "DELETE FROM copy_cache WHERE created_at < datetime('now', '-1 hour')"
        )
        if self.admin_id:
            conn.execute(
                "INSERT OR IGNORE INTO whitelist (user_id, username, approved_by) "
                "VALUES (?, ?, ?)",
                (self.admin_id, "admin", self.admin_id),
            )
            conn.execute(
                "UPDATE whitelist SET username = '' "
                "WHERE user_id != ? AND username != '' AND username != 'admin'",
                (self.admin_id,),
            )
        conn.commit()
        logger.info("SQLiteStorage initialized at %s", self.db_path)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        key_str = self._key_str(key)
        conn = self._get_conn()
        if state is None:
            state_val = None
        elif isinstance(state, str):
            state_val = state
        else:
            state_val = state.state if hasattr(state, "state") else str(state)
        existing = conn.execute(
            "SELECT data FROM fsm_states WHERE key = ?", (key_str,)
        ).fetchone()
        existing_data = existing["data"] if existing else None
        conn.execute(
            "INSERT OR REPLACE INTO fsm_states (key, state, data, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (key_str, state_val, existing_data),
        )
        conn.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        key_str = self._key_str(key)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT state FROM fsm_states WHERE key = ?", (key_str,)
        ).fetchone()
        if row and row["state"]:
            return row["state"]
        return None

    async def set_data(self, key: StorageKey, data: dict) -> None:
        key_str = self._key_str(key)
        conn = self._get_conn()
        data_json = json.dumps(data, default=str)
        existing = conn.execute(
            "SELECT state FROM fsm_states WHERE key = ?", (key_str,)
        ).fetchone()
        existing_state = existing["state"] if existing else None
        conn.execute(
            "INSERT OR REPLACE INTO fsm_states (key, state, data, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (key_str, existing_state, data_json),
        )
        conn.commit()

    async def get_data(self, key: StorageKey) -> dict:
        key_str = self._key_str(key)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT data FROM fsm_states WHERE key = ?", (key_str,)
        ).fetchone()
        if row and row["data"]:
            return json.loads(row["data"])
        return {}

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SQLiteStorage connection closed")

    def log_generation(self, user_id: int, username: str | None, category: str, has_competitor: bool) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO generation_log (user_id, username, category, has_competitor) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username or "", category, 1 if has_competitor else 0),
        )
        conn.commit()

    def get_recent_activity(self, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT user_id, username, category, has_competitor, created_at "
            "FROM generation_log "
            "WHERE created_at > datetime('now', '-7 days') "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_whitelisted(self, user_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def add_to_whitelist(self, user_id: int, username: str, approved_by: int) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO whitelist (user_id, username, approved_by) "
            "VALUES (?, ?, ?)",
            (user_id, username, approved_by),
        )
        conn.commit()

    def remove_from_whitelist(self, user_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        conn.commit()

    def get_whitelist_users(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT user_id, username, created_at FROM whitelist ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    def put_copy(self, key: str, text: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO copy_cache (key, text, created_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, text),
        )
        conn.commit()

    def get_copy(self, key: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT text FROM copy_cache "
            "WHERE key = ? AND created_at > datetime('now', '-1 hour')",
            (key,),
        ).fetchone()
        if row is None:
            conn.execute("DELETE FROM copy_cache WHERE key = ?", (key,))
            conn.commit()
            return None
        conn.execute("DELETE FROM copy_cache WHERE key = ?", (key,))
        conn.commit()
        return row["text"]
