"""群聊上下文的 SQLite 存储（消息、摘要、token 用量）。

单连接 + 线程锁：调用方通过 ``asyncio.to_thread`` 访问，避免阻塞事件循环。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from deltaforce_bot.models.chat import StoredMessage

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    nickname TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    message_id INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_group_id ON messages(group_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_group_message_id
    ON messages(group_id, message_id) WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS summaries (
    group_id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    group_id INTEGER,
    user_id INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);
"""


class ChatMemory:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---- 消息 ----
    def add_message(
        self,
        group_id: int,
        user_id: int,
        nickname: str,
        role: str,
        content: str,
        *,
        message_id: int | None = None,
        created_at: float | None = None,
    ) -> int:
        created = time.time() if created_at is None else float(created_at)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                "(group_id, user_id, nickname, role, content, message_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (group_id, user_id, nickname or "", role, content or "", message_id, created),
            )
            return int(cursor.lastrowid or 0)

    def recent_messages(
        self,
        group_id: int,
        *,
        limit: int = 30,
        max_chars: int = 6000,
        exclude_message_id: int | None = None,
    ) -> list[StoredMessage]:
        sql = (
            "SELECT id, group_id, user_id, nickname, role, content, message_id, created_at"
            " FROM messages WHERE group_id = ?"
        )
        params: list[object] = [group_id]
        if exclude_message_id:
            sql += " AND (message_id IS NULL OR message_id != ?)"
            params.append(exclude_message_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        selected: list[StoredMessage] = []
        used = 0
        for row in rows:
            content = row["content"] or ""
            if selected and used + len(content) > max_chars:
                break
            selected.append(_row_to_message(row))
            used += len(content)
        selected.reverse()
        return selected

    def max_message_id(self, group_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return int(row[0] or 0)

    def messages_up_to(self, group_id: int, max_id: int) -> list[StoredMessage]:
        """按时间顺序取该群 id <= max_id 的全部消息（摘要用，不做字符截断）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, group_id, user_id, nickname, role, content, message_id, created_at"
                " FROM messages WHERE group_id = ? AND id <= ? ORDER BY id ASC",
                (group_id, int(max_id)),
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def message_count(self, group_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return int(row[0] or 0)

    def delete_up_to(self, group_id: int, max_id: int, *, delete_summary: bool = False) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM messages WHERE group_id = ? AND id <= ?",
                (group_id, int(max_id)),
            )
            deleted = int(cursor.rowcount or 0)
            if delete_summary:
                self._conn.execute("DELETE FROM summaries WHERE group_id = ?", (group_id,))
        return deleted

    def clear(self, group_id: int) -> int:
        """删除该群全部消息和摘要，返回删除的消息条数。"""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM messages WHERE group_id = ?", (group_id,))
            deleted = int(cursor.rowcount or 0)
            self._conn.execute("DELETE FROM summaries WHERE group_id = ?", (group_id,))
        return deleted

    # ---- 摘要 ----
    def get_summary(self, group_id: int) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM summaries WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return str(row[0] or "") if row else ""

    def set_summary(self, group_id: int, content: str, *, created_at: float | None = None) -> None:
        created = time.time() if created_at is None else float(created_at)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO summaries(group_id, content, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(group_id) DO UPDATE SET content = excluded.content,"
                " created_at = excluded.created_at",
                (group_id, content, created),
            )

    # ---- 用量 ----
    def add_usage(
        self,
        day: str,
        *,
        group_id: int | None = None,
        user_id: int | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "",
        created_at: float | None = None,
    ) -> None:
        created = time.time() if created_at is None else float(created_at)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO usage"
                "(day, group_id, user_id, prompt_tokens, completion_tokens, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    day,
                    group_id,
                    user_id,
                    max(int(prompt_tokens), 0),
                    max(int(completion_tokens), 0),
                    model or "",
                    created,
                ),
            )

    def tokens_today(self, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0)"
                " FROM usage WHERE day = ?",
                (day,),
            ).fetchone()
        return int(row[0] or 0)

    # ---- 清理/关闭 ----
    def prune_before(self, cutoff: float) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            deleted = int(cursor.rowcount or 0)
            self._conn.execute("DELETE FROM usage WHERE created_at < ?", (cutoff,))
        return deleted

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None  # type: ignore[assignment]


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=int(row["id"]),
        group_id=int(row["group_id"]),
        user_id=int(row["user_id"]),
        nickname=str(row["nickname"] or ""),
        role=str(row["role"]),
        content=str(row["content"] or ""),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        created_at=float(row["created_at"]),
    )
