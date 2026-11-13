"""直播订阅的独立 SQLite 存储（按群订阅）。

每个群维护自己的订阅列表：同一个 UID 可以同时被 A 群和 B 群订阅，
开播时只推送到实际订阅了该 UID 的群。旧版本的全局订阅表会在初始化时
重命名为 ``live_subscriptions_global_legacy``，不自动分配群，需要重新按群订阅。
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

_SCHEMA_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS live_subscriptions (
    uid TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    room_id TEXT NOT NULL DEFAULT '',
    uname TEXT NOT NULL DEFAULT '',
    added_by INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (uid, group_id)
);
CREATE INDEX IF NOT EXISTS idx_live_subscriptions_group
    ON live_subscriptions(group_id);
"""

_SCHEMA_STATES = """
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
"""

_SCHEMA_NOTIFICATIONS = """
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
            self._migrate_subscriptions_locked()
            self._conn.executescript(_SCHEMA_STATES)
            self._conn.executescript(_SCHEMA_NOTIFICATIONS)
            self._conn.commit()

    def _migrate_subscriptions_locked(self) -> None:
        table_exists = self._conn.execute(  # type: ignore[union-attr]
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'live_subscriptions'"
        ).fetchone()
        if table_exists is not None:
            columns = {
                str(row["name"])
                for row in self._conn.execute(  # type: ignore[union-attr]
                    "PRAGMA table_info(live_subscriptions)"
                ).fetchall()
            }
            if "group_id" not in columns:
                legacy = "live_subscriptions_global_legacy"
                if self._conn.execute(  # type: ignore[union-attr]
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (legacy,),
                ).fetchone() is not None:
                    legacy = f"{legacy}_{int(time.time())}"
                self._conn.execute(  # type: ignore[union-attr]
                    f"ALTER TABLE live_subscriptions RENAME TO {legacy}"
                )
                logger.warning(
                    "直播订阅已切换为按群订阅；旧全局订阅表保留在 %s，"
                    "旧订阅不会自动分配到群，请按群重新订阅",
                    legacy,
                )
        self._conn.executescript(_SCHEMA_SUBSCRIPTIONS)  # type: ignore[union-attr]

    # ---------- 订阅（按群） ----------
    def add_subscription(self, subscription: LiveSubscription, *, now: float | None = None) -> bool:
        added = time.time() if now is None else float(now)
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO live_subscriptions"
                "(uid, group_id, room_id, uname, added_by, added_at, enabled)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    subscription.uid,
                    int(subscription.group_id),
                    subscription.room_id,
                    subscription.uname,
                    int(subscription.added_by),
                    float(subscription.added_at or added),
                    1 if subscription.enabled else 0,
                ),
            )
            return int(cursor.rowcount or 0) > 0

    def remove_subscription(self, uid: str, group_id: int) -> bool:
        with self._lock, self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(
                "DELETE FROM live_subscriptions WHERE uid = ? AND group_id = ?",
                (str(uid), int(group_id)),
            )
            return int(cursor.rowcount or 0) > 0

    def get_subscription(self, uid: str, group_id: int) -> LiveSubscription | None:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT uid, group_id, room_id, uname, added_by, added_at, enabled"
                " FROM live_subscriptions WHERE uid = ? AND group_id = ?",
                (str(uid), int(group_id)),
            ).fetchone()
        return _row_to_subscription(row) if row is not None else None

    def list_subscriptions(self, group_id: int | None = None) -> list[LiveSubscription]:
        sql = (
            "SELECT uid, group_id, room_id, uname, added_by, added_at, enabled"
            " FROM live_subscriptions"
        )
        params: list[object] = []
        if group_id is not None:
            sql += " WHERE group_id = ?"
            params.append(int(group_id))
        sql += " ORDER BY added_at ASC, uid ASC, group_id ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()  # type: ignore[union-attr]
        return [_row_to_subscription(row) for row in rows]

    def count_subscriptions(self, group_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM live_subscriptions"
        params: list[object] = []
        if group_id is not None:
            sql += " WHERE group_id = ?"
            params.append(int(group_id))
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()  # type: ignore[union-attr]
        return int(row[0] or 0)

    def count_subscriptions_for_uid(self, uid: str) -> int:
        with self._lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT COUNT(*) FROM live_subscriptions WHERE uid = ?",
                (str(uid),),
            ).fetchone()
        return int(row[0] or 0)

    def update_subscription_meta(self, uid: str, *, room_id: str, uname: str) -> None:
        """同一 UID 在多个群的订阅共用主播信息，全部更新。"""
        with self._lock, self._conn:  # type: ignore[union-attr]
            self._conn.execute(
                "UPDATE live_subscriptions SET room_id = ?, uname = ? WHERE uid = ?",
                (room_id, uname, str(uid)),
            )

    # ---------- 状态（按 UID 共享） ----------
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

    # ---------- 通知去重（按群） ----------
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
        group_id=int(row["group_id"]),
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
