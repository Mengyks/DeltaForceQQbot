"""直播订阅的独立 SQLite 存储。

轻量方案：单个文件、三张小表、WAL 模式；不引入 MySQL/Redis，也不存图片。
轮询与群命令可能同时读写，唯一约束负责保证“同一场直播每个群只推一次”。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from deltaforce_bot.models.live import (
    LiveState,
    LiveStatus,
    LiveSubscription,
)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_subscriptions (
    uid TEXT PRIMARY KEY,
    room_id TEXT NOT NULL DEFAULT '',
    uname TEXT NOT NULL DEFAULT '',
    added_by INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS live_states (
    uid TEXT PRIMARY KEY,
    room_id TEXT NOT NULL DEFAULT '',
    uname TEXT NOT NULL DEFAULT '',
    live_status INTEGER NOT NULL DEFAULT 0,
    live_time INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    cover TEXT NOT NULL DEFAULT '',
    online INTEGER NOT NULL DEFAULT 0,
    checked_at REAL NOT NULL,
    missing_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS live_notifications (
    uid TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    session_key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'push',
    notified_at REAL NOT NULL,
    PRIMARY KEY (uid, group_id, session_key)
);

CREATE INDEX IF NOT EXISTS idx_live_notifications_at
    ON live_notifications(notified_at);
"""


class LiveStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(self.path), timeout=5, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-2000")  # 约 2MB，照顾小内存云主机
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------- 订阅 ----------
    def add_subscription(self, subscription: LiveSubscription, *, now: float | None = None) -> bool:
        added = time.time() if now is None else float(now)
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO live_subscriptions"
                "(uid, room_id, uname, added_by, added_at, enabled)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    subscription.uid,
                    subscription.room_id,
                    subscription.uname,
                    int(subscription.added_by),
                    float(subscription.added_at or added),
                    1 if subscription.enabled else 0,
                ),
            )
            return int(cursor.rowcount or 0) > 0

    def remove_subscription(self, uid: str) -> bool:
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute("DELETE FROM live_subscriptions WHERE uid = ?", (str(uid),))
            return int(cursor.rowcount or 0) > 0

    def get_subscription(self, uid: str) -> LiveSubscription | None:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT uid, room_id, uname, added_by, added_at, enabled"
                " FROM live_subscriptions WHERE uid = ?",
                (str(uid),),
            ).fetchone()
        return _row_to_subscription(row) if row is not None else None

    def list_subscriptions(self) -> list[LiveSubscription]:
        with self._lock:
            rows = self._conn.execute(  # type: ignore[union-attr]
                "SELECT uid, room_id, uname, added_by, added_at, enabled"
                " FROM live_subscriptions ORDER BY added_at ASC, uid ASC"
            ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    def count_subscriptions(self) -> int:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT COUNT(*) FROM live_subscriptions"
            ).fetchone()
        return int(row[0] or 0)

    def update_subscription_meta(self, uid: str, *, room_id: str, uname: str) -> None:
        with self._lock, self._conn:  # type: ignore[union-attr]
            self._conn.execute(
                "UPDATE live_subscriptions SET room_id = ?, uname = ? WHERE uid = ?",
                (room_id, uname, str(uid)),
            )

    # ---------- 状态 ----------
    def upsert_state(self, state: LiveState) -> None:
        status = state.status
        with self._lock, self._conn:  # type: ignore[union-attr]
            self._conn.execute(
                "INSERT INTO live_states"
                "(uid, room_id, uname, live_status, live_time, title, cover, online, checked_at, missing_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(uid) DO UPDATE SET"
                " room_id = excluded.room_id,"
                " uname = excluded.uname,"
                " live_status = excluded.live_status,"
                " live_time = excluded.live_time,"
                " title = excluded.title,"
                " cover = excluded.cover,"
                " online = excluded.online,"
                " checked_at = excluded.checked_at,"
                " missing_count = excluded.missing_count",
                (
                    status.uid,
                    status.room_id,
                    status.uname,
                    int(status.live_status),
                    int(status.live_time),
                    status.title,
                    status.cover,
                    int(status.online),
                    float(state.checked_at),
                    int(state.missing_count),
                ),
            )

    def get_state(self, uid: str) -> LiveState | None:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT uid, room_id, uname, live_status, live_time, title, cover,"
                " online, checked_at, missing_count FROM live_states WHERE uid = ?",
                (str(uid),),
            ).fetchone()
        return _row_to_state(row) if row is not None else None

    def list_states(self) -> dict[str, LiveState]:
        with self._lock:
            rows = self._conn.execute(  # type: ignore[union-attr]
                "SELECT uid, room_id, uname, live_status, live_time, title, cover,"
                " online, checked_at, missing_count FROM live_states"
            ).fetchall()
        return {str(row["uid"]): _row_to_state(row) for row in rows}

    def delete_state(self, uid: str) -> None:
        with self._lock, self._conn:  # type: ignore[union-attr]
            self._conn.execute("DELETE FROM live_states WHERE uid = ?", (str(uid),))

    # ---------- 通知去重 ----------
    def mark_notification(
        self,
        uid: str,
        group_id: int,
        session_key: str,
        *,
        kind: str = "push",
        notified_at: float | None = None,
    ) -> None:
        at = time.time() if notified_at is None else float(notified_at)
        with self._lock, self._conn:  # type: ignore[union-attr]
            self._conn.execute(
                "INSERT OR IGNORE INTO live_notifications"
                "(uid, group_id, session_key, kind, notified_at) VALUES (?, ?, ?, ?, ?)",
                (str(uid), int(group_id), str(session_key), kind, at),
            )

    def was_notified(self, uid: str, group_id: int, session_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT 1 FROM live_notifications"
                " WHERE uid = ? AND group_id = ? AND session_key = ?",
                (str(uid), int(group_id), str(session_key)),
            ).fetchone()
        return row is not None

    def delete_notifications_for_session(self, uid: str, session_key: str) -> int:
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(
                "DELETE FROM live_notifications WHERE uid = ? AND session_key = ?",
                (str(uid), str(session_key)),
            )
            return int(cursor.rowcount or 0)

    def prune_notifications_before(self, cutoff: float) -> int:
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(
                "DELETE FROM live_notifications WHERE notified_at < ?",
                (float(cutoff),),
            )
            return int(cursor.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None


def _row_to_subscription(row: sqlite3.Row) -> LiveSubscription:
    return LiveSubscription(
        uid=str(row["uid"]),
        room_id=str(row["room_id"] or ""),
        uname=str(row["uname"] or ""),
        added_by=int(row["added_by"] or 0),
        added_at=float(row["added_at"] or 0.0),
        enabled=bool(row["enabled"]),
    )


def _row_to_state(row: sqlite3.Row) -> LiveState:
    status = LiveStatus(
        uid=str(row["uid"]),
        room_id=str(row["room_id"] or ""),
        uname=str(row["uname"] or ""),
        live_status=int(row["live_status"] or 0),
        live_time=int(row["live_time"] or 0),
        title=str(row["title"] or ""),
        cover=str(row["cover"] or ""),
        online=int(row["online"] or 0),
    )
    return LiveState(
        status=status,
        checked_at=float(row["checked_at"] or 0.0),
        missing_count=int(row["missing_count"] or 0),
    )
