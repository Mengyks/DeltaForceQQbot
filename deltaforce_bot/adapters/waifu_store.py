"""猪结婚的每日配对存储（独立 SQLite）。

每天每个群形成若干互不重叠的配对：
- ``waifu_pairs`` 一对一行，存拼好的图片；
- ``waifu_members`` 每个成员一行（A→B 和 B→A），用主键保证
  同一天同一个群里的一个人只能出现在一对里。

刻意不复用聊天/每日密码数据库；旧数据按天清理。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from deltaforce_bot.models.waifu import WaifuResult

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS waifu_pairs (
    pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    image BLOB NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS waifu_members (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    partner_id INTEGER NOT NULL,
    partner_name TEXT NOT NULL,
    pair_id INTEGER NOT NULL,
    PRIMARY KEY (day, group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_waifu_pairs_day_group
    ON waifu_pairs(day, group_id);
"""


class WaifuPairConflict(RuntimeError):
    """并发配对时目标已被别人占用（唯一约束冲突）。"""


class WaifuStore:
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

    def get_partner(self, day: str, group_id: int, user_id: int) -> WaifuResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT m.partner_id, m.partner_name, p.image"
                " FROM waifu_members m"
                " JOIN waifu_pairs p ON p.pair_id = m.pair_id"
                " WHERE m.day = ? AND m.group_id = ? AND m.user_id = ?",
                (day, int(group_id), int(user_id)),
            ).fetchone()
        if row is None:
            return None
        return WaifuResult(
            partner_id=int(row["partner_id"]),
            partner_name=str(row["partner_name"]),
            image=bytes(row["image"]),
        )

    def paired_users(self, day: str, group_id: int) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM waifu_members WHERE day = ? AND group_id = ?",
                (day, int(group_id)),
            ).fetchall()
        return {int(row["user_id"]) for row in rows}

    def create_pair(
        self,
        day: str,
        group_id: int,
        user_a: int,
        name_a: str,
        user_b: int,
        name_b: str,
        image: bytes,
        *,
        created_at: float | None = None,
    ) -> None:
        """写入 A-B 配对；任意一方已被配对时抛 WaifuPairConflict。"""
        created = time.time() if created_at is None else float(created_at)
        try:
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "INSERT INTO waifu_pairs(day, group_id, image, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (day, int(group_id), sqlite3.Binary(image), created),
                )
                pair_id = int(cursor.lastrowid)
                self._conn.executemany(
                    "INSERT INTO waifu_members"
                    "(day, group_id, user_id, partner_id, partner_name, pair_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (day, int(group_id), int(user_a), int(user_b), name_b, pair_id),
                        (day, int(group_id), int(user_b), int(user_a), name_a, pair_id),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise WaifuPairConflict(f"{user_a}/{user_b} 已被配对") from exc

    def pair_count(self, day: str, group_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM waifu_pairs WHERE day = ? AND group_id = ?",
                (day, int(group_id)),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def prune_before(self, day: str) -> int:
        """删除早于 day（YYYY-MM-DD）的配对和成员行；返回删除的配对数。"""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM waifu_pairs WHERE day < ?",
                (day,),
            ).fetchone()
            self._conn.execute("DELETE FROM waifu_members WHERE day < ?", (day,))
            self._conn.execute("DELETE FROM waifu_pairs WHERE day < ?", (day,))
        return int(row["n"]) if row is not None else 0

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None  # type: ignore[assignment]
