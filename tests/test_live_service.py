"""直播订阅服务测试：添加/删除/查询、开播转换、去重、重启、缺数据。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deltaforce_bot.adapters.live_bilibili import LiveApiError
from deltaforce_bot.adapters.live_store import LiveStore
from deltaforce_bot.models.live import LiveStatus
from deltaforce_bot.services.live_watch import (
    LiveWatchService,
    format_live_notification_text,
    format_live_status,
    format_live_subscriptions,
    normalize_live_uid,
)
from tests.support import make_live_settings

GROUP_A = 30000
GROUP_B = 30001


class FakeLiveClient:
    def __init__(self, statuses: dict[str, LiveStatus] | None = None) -> None:
        self.statuses = dict(statuses or {})
        self.error: Exception | None = None
        self.calls: list[list[str]] = []

    async def fetch_statuses(self, uids):
        self.calls.append([str(uid) for uid in uids])
        if self.error is not None:
            raise self.error
        return {uid: self.statuses[uid] for uid in uids if uid in self.statuses}

    async def fetch_status(self, uid: str):
        self.calls.append([str(uid)])
        if self.error is not None:
            raise self.error
        return self.statuses.get(str(uid))

    async def aclose(self) -> None:
        return None


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def status(
    *,
    uid: str = "1",
    room_id: str = "10",
    uname: str = "测试主播",
    live_status: int = 0,
    live_time: int = 0,
    title: str = "测试标题",
    cover: str = "https://example.test/cover.jpg",
) -> LiveStatus:
    return LiveStatus(
        uid=uid,
        room_id=room_id,
        uname=uname,
        live_status=live_status,
        live_time=live_time,
        title=title,
        cover=cover,
    )


def make_service(
    tmp_path: Path,
    statuses: dict[str, LiveStatus] | None = None,
    *,
    clock: Clock | None = None,
    **overrides,
) -> tuple[LiveWatchService, LiveStore, FakeLiveClient]:
    settings = make_live_settings(tmp_path, **overrides)
    store = LiveStore(settings.live_db_path)
    client = FakeLiveClient(statuses)
    service = LiveWatchService(settings, client, store, clock=clock or Clock())
    return service, store, client


async def test_normalize_uid() -> None:
    assert normalize_live_uid("672328094") == "672328094"
    assert normalize_live_uid(" ６７２３２８０９４ ") == "672328094"
    with pytest.raises(ValueError):
        normalize_live_uid("")
    with pytest.raises(ValueError):
        normalize_live_uid("abc")
    with pytest.raises(ValueError):
        normalize_live_uid("0")


async def test_add_offline_and_live_suppression(tmp_path: Path) -> None:
    service, store, _ = make_service(tmp_path, {"1": status(), "2": status(uid="2", room_id="20")})
    try:
        result = await service.add("1", user_id=123)
        assert result.ok is True
        assert "已订阅" in result.message
        assert store.get_subscription("1") is not None
        state = store.get_state("1")
        assert state is not None and state.is_live is False

        service2, store2, _ = make_service(
            tmp_path / "second",
            {"2": status(uid="2", live_status=1, live_time=2000)},
        )
        try:
            result2 = await service2.add("2")
            assert result2.ok is True
            assert "正在直播" in result2.message
            for group_id in (GROUP_A, GROUP_B):
                assert store2.was_notified("2", group_id, "2000") is True
            polled = await service2.poll_once()
            assert polled.notifications == []
        finally:
            await service2.aclose()
    finally:
        await service.aclose()


async def test_add_rejects_no_room_duplicate_and_invalid(tmp_path: Path) -> None:
    service, store, _ = make_service(tmp_path, {"1": status()})
    try:
        no_room = await service.add("999", user_id=1)
        assert no_room.ok is False
        assert "没有找到" in no_room.message

        invalid = await service.add("abc")
        assert invalid.ok is False
        assert "纯数字" in invalid.message

        first = await service.add("1")
        assert first.ok is True
        second = await service.add("1")
        assert second.ok is False
        assert "已经订阅过" in second.message
    finally:
        await service.aclose()


async def test_add_respects_limit(tmp_path: Path) -> None:
    service, _, _ = make_service(
        tmp_path,
        {"1": status(), "2": status(uid="2", room_id="20")},
        live_max_subscriptions=1,
    )
    try:
        assert (await service.add("1")).ok is True
        result = await service.add("2")
        assert result.ok is False
        assert "上限" in result.message
    finally:
        await service.aclose()


async def test_poll_transition_notifies_each_group_once(tmp_path: Path) -> None:
    service, store, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        client.statuses["1"] = status(live_status=1, live_time=1792912598, title="开播了")

        first = await service.poll_once()
        assert first.checked == 1
        assert first.live == 1
        assert {item.group_id for item in first.notifications} == {GROUP_A, GROUP_B}
        notification = first.notifications[0]
        assert notification.session_key == "1792912598"
        assert notification.uname == "测试主播"
        assert notification.room_url == "https://live.bilibili.com/10"

        await service.mark_notified(first.notifications[0])
        second = await service.poll_once()
        assert {item.group_id for item in second.notifications} == {GROUP_B}

        await service.mark_notified(second.notifications[0])
        third = await service.poll_once()
        assert third.notifications == []
    finally:
        await service.aclose()


async def test_down_then_up_again_notifies_again(tmp_path: Path) -> None:
    service, _, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        client.statuses["1"] = status(live_status=1, live_time=1000)
        first = await service.poll_once()
        assert len(first.notifications) == 2
        for item in first.notifications:
            await service.mark_notified(item)

        client.statuses["1"] = status(live_status=0)
        assert (await service.poll_once()).notifications == []

        client.statuses["1"] = status(live_status=1, live_time=2000)
        second = await service.poll_once()
        assert len(second.notifications) == 2
        assert all(item.session_key == "2000" for item in second.notifications)
    finally:
        await service.aclose()


async def test_round_is_not_live(tmp_path: Path) -> None:
    service, store, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        client.statuses["1"] = status(live_status=2)
        result = await service.poll_once()
        assert result.notifications == []
        state = store.get_state("1")
        assert state is not None and state.status.live_status == 2
    finally:
        await service.aclose()


async def test_api_error_does_not_change_state(tmp_path: Path) -> None:
    service, store, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        before = store.get_state("1")
        client.error = LiveApiError("boom")
        with pytest.raises(LiveApiError):
            await service.poll_once()
        after = store.get_state("1")
        assert after is not None and before is not None
        assert after.checked_at == before.checked_at
        assert after.status.live_status == before.status.live_status
    finally:
        await service.aclose()


async def test_missing_uid_needs_three_polls_to_go_offline(tmp_path: Path) -> None:
    service, store, client = make_service(
        tmp_path,
        {"1": status(live_status=1, live_time=1000)},
    )
    try:
        await service.add("1")  # 添加时正在直播，抑制当前场次
        client.statuses.clear()

        first = await service.poll_once()
        assert first.missing == ["1"]
        state = store.get_state("1")
        assert state is not None and state.is_live is True and state.missing_count == 1

        second = await service.poll_once()
        state = store.get_state("1")
        assert state is not None and state.is_live is True and state.missing_count == 2

        third = await service.poll_once()
        state = store.get_state("1")
        assert state is not None and state.is_live is False and state.missing_count == 3
        assert third.notifications == []
    finally:
        await service.aclose()


async def test_restart_does_not_duplicate(tmp_path: Path) -> None:
    clock = Clock()
    service, _store, client = make_service(tmp_path, {"1": status()}, clock=clock)
    await service.add("1")
    client.statuses["1"] = status(live_status=1, live_time=1000)
    result = await service.poll_once()
    assert len(result.notifications) == 2
    for item in result.notifications:
        await service.mark_notified(item)
    await service.aclose()

    # 模拟重启：同一个 SQLite 文件，新 service/store
    settings = make_live_settings(tmp_path)
    store2 = LiveStore(settings.live_db_path)
    client2 = FakeLiveClient({"1": status(live_status=1, live_time=1000)})
    service2 = LiveWatchService(settings, client2, store2, clock=clock)
    try:
        restarted = await service2.poll_once()
        assert restarted.notifications == []
    finally:
        await service2.aclose()


async def test_first_observation_without_state_does_not_notify(tmp_path: Path) -> None:
    service, store, client = make_service(
        tmp_path,
        {"1": status(live_status=1, live_time=1000)},
    )
    try:
        # 手工写入订阅但没有状态（模拟旧数据/迁移）
        from deltaforce_bot.models.live import LiveSubscription

        await asyncio.to_thread(
            store.add_subscription,
            LiveSubscription(uid="1", room_id="10", uname="主播", added_at=1.0),
        )
        result = await service.poll_once()
        assert result.notifications == []
        state = store.get_state("1")
        assert state is not None and state.is_live is True

        # 第二轮也不能补推；首次观察应写入抑制记录
        second = await service.poll_once()
        assert second.notifications == []
        for group_id in (GROUP_A, GROUP_B):
            assert store.was_notified("1", group_id, "1000") is True
    finally:
        await service.aclose()


async def test_session_change_while_live_is_suppressed(tmp_path: Path) -> None:
    service, store, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        client.statuses["1"] = status(live_status=1, live_time=1000)
        first = await service.poll_once()
        assert len(first.notifications) == 2
        for item in first.notifications:
            await service.mark_notified(item)

        client.statuses["1"] = status(live_status=1, live_time=2000)
        second = await service.poll_once()
        assert second.notifications == []
        for group_id in (GROUP_A, GROUP_B):
            assert store.was_notified("1", group_id, "2000") is True
    finally:
        await service.aclose()


async def test_list_and_format(tmp_path: Path) -> None:
    service, _, client = make_service(tmp_path, {"1": status()})
    try:
        await service.add("1")
        views = await service.list_subscriptions()
        assert len(views) == 1
        assert views[0].subscription.uid == "1"
        assert "全局直播订阅" in format_live_subscriptions(views)
        assert "未开播" in format_live_subscriptions(views)

        client.statuses["1"] = status(live_status=1, live_time=1000)
        live = await service.query("1")
        assert live is not None
        text = format_live_status(live)
        assert "直播中" in text
        assert "https://live.bilibili.com/10" in text

        notification = (await service.poll_once()).notifications[0]
        assert "【B站开播】" in format_live_notification_text(notification)
        assert "https://live.bilibili.com/10" in format_live_notification_text(notification)
    finally:
        await service.aclose()


async def test_runtime_wires_live_service(tmp_path: Path) -> None:
    from deltaforce_bot.runtime import Runtime

    runtime = Runtime(make_live_settings(tmp_path))
    try:
        assert runtime.live is not None
        assert runtime.live_client is not None
        assert runtime.live_store is not None
    finally:
        await runtime.aclose()

    runtime = Runtime(make_live_settings(tmp_path, live_enabled=False))
    try:
        assert runtime.live is None
    finally:
        await runtime.aclose()
