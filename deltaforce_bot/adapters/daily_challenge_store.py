"""每日天尊挑战的每日缓存（独立 SQLite）。

键是 ``(day, group_id, user_id)``：同一个群友当天重复查询返回同一张图，
不同群/不同人各自一份；图片直接存 BLOB，避免重复拼图。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from deltaforce_bot.models.daily_challenge import DailyChallengeResult

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_challenges (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    monster_name TEXT NOT NULL,
    weapon_name TEXT NOT NULL,
    image BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (day, group_id, user_id)
);
"""


class DailyChallengeStore:
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

    def get(self, day: str, group_id: int, user_id: int) -> DailyChallengeResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT monster_name, weapon_name, image FROM daily_challenges"
                " WHERE day = ? AND group_id = ? AND user_id = ?",
                (day, int(group_id), int(user_id)),
            ).fetchone()
        if row is None:
            return None
        return DailyChallengeResult(
            monster_name=str(row["monster_name"]),
            weapon_name=str(row["weapon_name"]),
            image=bytes(row["image"]),
        )

    def save(
        self,
        day: str,
        group_id: int,
        user_id: int,
        monster_name: str,
        weapon_name: str,
        image: bytes,
        *,
        created_at: float | None = None,
    ) -> None:
        created = time.time() if created_at is None else float(created_at)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_challenges"
                "(day, group_id, user_id, monster_name, weapon_name, image, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    day,
                    int(group_id),
                    int(user_id),
                    monster_name,
                    weapon_name,
                    sqlite3.Binary(image),
                    created,
                ),
            )

    def prune_before(self, day: str) -> int:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM daily_challenges WHERE day < ?",
                (day,),
            ).fetchone()
            self._conn.execute("DELETE FROM daily_challenges WHERE day < ?", (day,))
        return int(row["n"]) if row is not None else 0

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None  # type: ignore[assignment]
