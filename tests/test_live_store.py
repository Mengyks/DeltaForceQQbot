"""直播订阅 SQLite 存储测试：订阅、状态、通知去重与清理。"""

from __future__ import annotations

from pathlib import Path

from deltaforce_bot.adapters.live_store import LiveStore
from deltaforce_bot.models.live import (
    LiveState,
    LiveStatus,
    LiveSubscription,
)


def make_store(tmp_path: Path) -> LiveStore:
    return LiveStore(tmp_path / "live.sqlite3")


def test_subscription_crud(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        sub = LiveSubscription(
            uid="672328094",
            room_id="22637261",
            uname="嘉然今天吃什么",
            added_by=123,
            added_at=100.0,
        )
        assert store.add_subscription(sub) is True
        assert store.add_subscription(sub) is False  # 唯一约束
        assert store.count_subscriptions() == 1

        loaded = store.get_subscription("672328094")
        assert loaded is not None
        assert loaded.uname == "嘉然今天吃什么"
        assert loaded.added_by == 123

        store.update_subscription_meta("672328094", room_id="999", uname="新名字")
        loaded = store.get_subscription("672328094")
        assert loaded is not None and loaded.room_id == "999" and loaded.uname == "新名字"

        assert [item.uid for item in store.list_subscriptions()] == ["672328094"]
        assert store.remove_subscription("672328094") is True
        assert store.remove_subscription("672328094") is False
        assert store.get_subscription("672328094") is None
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
        store.mark_notification("672328094", 30000, "1792912598", notified_at=1.0)
        assert store.was_notified("672328094", 30000, "1792912598") is True
        assert store.was_notified("672328094", 30000, "other") is False
        assert store.was_notified("672328094", 30001, "1792912598") is False

        store.mark_notification("672328094", 30001, "1792912598", kind="suppress", notified_at=2.0)
        assert store.was_notified("672328094", 30001, "1792912598") is True

        # 重复写入不会改变原记录
        store.mark_notification("672328094", 30000, "1792912598", kind="suppress", notified_at=3.0)
        assert store.was_notified("672328094", 30000, "1792912598") is True

        deleted = store.delete_notifications_for_session("672328094", "1792912598")
        assert deleted == 2
        assert store.was_notified("672328094", 30000, "1792912598") is False
    finally:
        store.close()


def test_prune_notifications(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.mark_notification("1", 30000, "old", notified_at=10.0)
        store.mark_notification("1", 30000, "new", notified_at=100.0)
        assert store.prune_notifications_before(50.0) == 1
        assert store.was_notified("1", 30000, "old") is False
        assert store.was_notified("1", 30000, "new") is True
    finally:
        store.close()


def test_persists_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite3"
    store = LiveStore(path)
    store.add_subscription(LiveSubscription(uid="1", room_id="2", uname="u", added_at=1.0))
    store.mark_notification("1", 30000, "session", notified_at=1.0)
    store.close()

    store = LiveStore(path)
    try:
        assert store.get_subscription("1") is not None
        assert store.was_notified("1", 30000, "session") is True
    finally:
        store.close()
