"""直播订阅 SQLite 存储测试：按群订阅、共享状态、通知去重与旧表迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from deltaforce_bot.adapters.live_store import LiveStore
from deltaforce_bot.models.live import (
    LiveState,
    LiveStatus,
    LiveSubscription,
)

GROUP_A = 30000
GROUP_B = 30001


def make_store(tmp_path: Path) -> LiveStore:
    return LiveStore(tmp_path / "live.sqlite3")


def make_sub(uid: str, group_id: int, *, uname: str = "主播") -> LiveSubscription:
    return LiveSubscription(
        uid=uid,
        group_id=group_id,
        room_id="22637261",
        uname=uname,
        added_by=123,
        added_at=100.0,
    )


def test_subscription_crud_per_group(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.add_subscription(make_sub("672328094", GROUP_A)) is True
        # 同一个 UID 可以在另一个群独立订阅
        assert store.add_subscription(make_sub("672328094", GROUP_B)) is True
        # 同一个群重复订阅被唯一约束拒绝
        assert store.add_subscription(make_sub("672328094", GROUP_A)) is False

        assert store.count_subscriptions(GROUP_A) == 1
        assert store.count_subscriptions(GROUP_B) == 1
        assert store.count_subscriptions() == 2
        assert store.count_subscriptions_for_uid("672328094") == 2

        loaded = store.get_subscription("672328094", GROUP_A)
        assert loaded is not None
        assert loaded.group_id == GROUP_A
        assert loaded.uname == "主播"

        assert [item.group_id for item in store.list_subscriptions(GROUP_A)] == [GROUP_A]
        assert [item.group_id for item in store.list_subscriptions(GROUP_B)] == [GROUP_B]
        assert len(store.list_subscriptions()) == 2

        store.update_subscription_meta("672328094", room_id="999", uname="新名字")
        for group_id in (GROUP_A, GROUP_B):
            loaded = store.get_subscription("672328094", group_id)
            assert loaded is not None
            assert loaded.room_id == "999" and loaded.uname == "新名字"

        assert store.remove_subscription("672328094", GROUP_A) is True
        assert store.get_subscription("672328094", GROUP_A) is None
        assert store.get_subscription("672328094", GROUP_B) is not None
        assert store.count_subscriptions_for_uid("672328094") == 1

        assert store.remove_subscription("672328094", GROUP_B) is True
        assert store.remove_subscription("672328094", GROUP_B) is False
        assert store.count_subscriptions() == 0
    finally:
        store.close()


def test_state_upsert_and_delete(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        status = LiveStatus(
            uid="672328094",
            room_id="22637261",
            uname="嘉然今天吃什么",
            live_status=1,
            live_time=1792912598,
            title="直播标题",
            cover="https://example.test/cover.jpg",
            online=120,
        )
        store.upsert_state(LiveState(status=status, checked_at=10.0))
        loaded = store.get_state("672328094")
        assert loaded is not None
        assert loaded.is_live is True
        assert loaded.status.live_time == 1792912598
        assert loaded.status.online == 120

        store.upsert_state(LiveState(status=status, checked_at=20.0, missing_count=2))
        loaded = store.get_state("672328094")
        assert loaded is not None
        assert loaded.checked_at == 20.0
        assert loaded.missing_count == 2

        states = store.list_states()
        assert set(states) == {"672328094"}

        store.delete_state("672328094")
        assert store.get_state("672328094") is None
    finally:
        store.close()


def test_notifications_are_unique_per_group_and_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.mark_notification("672328094", GROUP_A, "1792912598", notified_at=1.0)
        assert store.was_notified("672328094", GROUP_A, "1792912598") is True
        assert store.was_notified("672328094", GROUP_A, "other") is False
        assert store.was_notified("672328094", GROUP_B, "1792912598") is False

        store.mark_notification("672328094", GROUP_B, "1792912598", kind="suppress", notified_at=2.0)
        assert store.was_notified("672328094", GROUP_B, "1792912598") is True

        store.mark_notification("672328094", GROUP_A, "1792912598", kind="suppress", notified_at=3.0)
        assert store.was_notified("672328094", GROUP_A, "1792912598") is True

        deleted = store.delete_notifications_for_session("672328094", "1792912598")
        assert deleted == 2
        assert store.was_notified("672328094", GROUP_A, "1792912598") is False
    finally:
        store.close()


def test_prune_notifications(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.mark_notification("1", GROUP_A, "old", notified_at=10.0)
        store.mark_notification("1", GROUP_A, "new", notified_at=100.0)
        assert store.prune_notifications_before(50.0) == 1
        assert store.was_notified("1", GROUP_A, "old") is False
        assert store.was_notified("1", GROUP_A, "new") is True
    finally:
        store.close()


def test_persists_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite3"
    store = LiveStore(path)
    store.add_subscription(make_sub("1", GROUP_A, uname="u"))
    store.mark_notification("1", GROUP_A, "session", notified_at=1.0)
    store.close()

    store = LiveStore(path)
    try:
        assert store.get_subscription("1", GROUP_A) is not None
        assert store.was_notified("1", GROUP_A, "session") is True
    finally:
        store.close()


def test_legacy_global_schema_is_renamed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE live_subscriptions (
            uid TEXT PRIMARY KEY,
            room_id TEXT NOT NULL DEFAULT '',
            uname TEXT NOT NULL DEFAULT '',
            added_by INTEGER NOT NULL DEFAULT 0,
            added_at REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO live_subscriptions(uid, room_id, uname, added_at)
        VALUES ('2', '1024', '旧全局订阅', 1.0);
        """
    )
    conn.commit()
    conn.close()

    store = LiveStore(path)
    try:
        # 旧全局订阅不能自动分配群，保留在 legacy 表；新表为空
        assert store.count_subscriptions() == 0
        legacy = store._conn.execute(  # noqa: SLF001
            "SELECT uid FROM live_subscriptions_global_legacy"
        ).fetchone()
        assert legacy is not None and legacy["uid"] == "2"
        columns = {
            row["name"]
            for row in store._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(live_subscriptions)"
            ).fetchall()
        }
        assert "group_id" in columns
    finally:
        store.close()
