"""每日密码的独立 SQLite 存储（快照缓存 + 推送去重）。

刻意不复用 ``data/chat.sqlite3``：聊天上下文清理不应影响每日密码，
每日密码的缓存/推送记录也不应进入聊天表。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from deltaforce_bot.models.daily_password import DailyPasswordSnapshot

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    day TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS push_log (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    pushed_at REAL NOT NULL,
    PRIMARY KEY (day, group_id)
);
"""


class DailyPasswordStore:
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

    # ---- 快照 ----
    def get_snapshot(self, day: str) -> DailyPasswordSnapshot | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM snapshots WHERE day = ?",
                (day,),
            ).fetchone()
        if row is None:
            return None
        try:
            return DailyPasswordSnapshot.from_json(str(row["payload"]))
        except ValueError as exc:
            logger.warning("每日密码缓存损坏（%s），忽略：%s", day, exc)
            return None

    def save_snapshot(
        self,
        snapshot: DailyPasswordSnapshot,
        *,
        fetched_at: float | None = None,
    ) -> None:
        fetched = time.time() if fetched_at is None else float(fetched_at)
        payload = snapshot.to_json()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO snapshots(day, source, updated_at, payload, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(day) DO UPDATE SET"
                " source = excluded.source,"
                " updated_at = excluded.updated_at,"
                " payload = excluded.payload,"
                " fetched_at = excluded.fetched_at",
                (snapshot.day, snapshot.source, snapshot.updated_at, payload, fetched),
            )

    # ---- 推送去重（同一天每个群只推一次）----
    def was_pushed(self, day: str, group_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM push_log WHERE day = ? AND group_id = ?",
                (day, int(group_id)),
            ).fetchone()
        return row is not None

    def mark_pushed(
        self,
        day: str,
        group_id: int,
        *,
        pushed_at: float | None = None,
    ) -> None:
        pushed = time.time() if pushed_at is None else float(pushed_at)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO push_log(day, group_id, pushed_at) VALUES (?, ?, ?)",
                (day, int(group_id), pushed),
            )

    def prune_before(self, day: str) -> None:
        """删除早于 day（YYYY-MM-DD）的快照和推送记录。"""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM snapshots WHERE day < ?", (day,))
            self._conn.execute("DELETE FROM push_log WHERE day < ?", (day,))

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None  # type: ignore[assignment]
