"""每日密码：免费源解析、当天缓存、冷却、推送窗口与排版。"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from deltaforce_bot.adapters.daily_password import (
    DailyPasswordClient,
    DailyPasswordError,
)
from deltaforce_bot.adapters.daily_password_store import DailyPasswordStore
from deltaforce_bot.models.daily_password import DailyPasswordEntry, DailyPasswordSnapshot
from deltaforce_bot.services.daily_password import (
    DailyPasswordService,
    format_daily_password,
    format_wait,
)
from tests.support import FIXTURES, make_settings

BJ = timezone(timedelta(hours=8))
TODAY = "2026-10-23"
MAP_NAMES = ["零号大坝", "长弓溪谷", "巴克什", "航天基地", "潮汐监狱", "AZ3"]


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def daily_settings(**overrides: Any):
    base: dict[str, Any] = {
        "daily_password_enabled": True,
        "daily_password_sources": "4399,eleven",
        "daily_password_timeout": 2.0,
        "daily_password_refresh_time": "00:05",
        "daily_password_push_time": "11:00",
        "daily_password_push_window_minutes": 60,
        "daily_password_query_cooldown": 300,
        "daily_password_history_days": 7,
        "daily_password_tz_offset": 8,
    }
    base.update(overrides)
    return make_settings(**base)


def make_client(payload_by_fragment: dict[str, Any], **overrides: Any) -> DailyPasswordClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for fragment, payload in payload_by_fragment.items():
            if fragment in url:
                if isinstance(payload, int):
                    return httpx.Response(payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DailyPasswordClient(daily_settings(**overrides), http=http)


def sample_snapshot(day: str = TODAY) -> DailyPasswordSnapshot:
    return DailyPasswordSnapshot(
        day=day,
        source="4399游戏盒",
        updated_at=f"{day}T00:00:00",
        entries=(
            DailyPasswordEntry(name="零号大坝", code="0396", location="主变电站右侧"),
            DailyPasswordEntry(name="长弓溪谷", code="4893", location="地图右下角"),
        ),
    )


class FakeDailyClient:
    def __init__(self) -> None:
        self.days: list[str] = []

    async def fetch(self, day: str) -> DailyPasswordSnapshot:
        self.days.append(day)
        return sample_snapshot(day)

    async def aclose(self) -> None:
        pass


# ---------- 数据源解析 / 换源 ----------
async def test_parse_4399_real_fixture() -> None:
    payload = load_fixture("daily_password_4399.json")
    day = str(payload["date"])
    client = make_client({"4399": payload})
    try:
        snapshot = await client.fetch(day)
    finally:
        await client.aclose()

    assert snapshot.day == day
    assert snapshot.source == "4399游戏盒"
    assert snapshot.updated_at == f"{day}T00:00:00"
    assert [entry.name for entry in snapshot.entries] == MAP_NAMES
    assert [entry.code for entry in snapshot.entries] == [
        "0396",
        "4893",
        "4356",
        "0114",
        "3468",
        "3525",
    ]
    assert snapshot.entries[0].location.startswith("主变电站右侧")
    assert snapshot.entries[0].image_url.startswith("https://mmbiz.qpic.cn/")
    assert "&amp;" not in snapshot.entries[0].image_url
    assert "&from=appmsg" in snapshot.entries[0].image_url


async def test_stale_4399_falls_back_to_eleven() -> None:
    stale = copy.deepcopy(load_fixture("daily_password_4399.json"))
    eleven = load_fixture("daily_password_eleven.json")
    today = eleven_fixture_day(eleven)
    stale["date"] = (
        datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=1)
    ).isoformat()
    client = make_client(
        {
            "4399": stale,
            "eleven": eleven,
        }
    )
    try:
        snapshot = await client.fetch(today)
    finally:
        await client.aclose()

    assert snapshot.source == "eleven.icu"
    assert snapshot.day == today
    assert [entry.name for entry in snapshot.entries] == MAP_NAMES
    assert all(entry.location == "" for entry in snapshot.entries)


def eleven_fixture_day(payload: dict[str, Any]) -> str:
    updated = next(iter(payload["data"]["bdData"].values()))["updated"]
    return datetime.strptime(str(updated)[:8], "%Y%m%d").date().isoformat()


async def test_all_sources_fail_raises() -> None:
    client = make_client({"4399": 500, "eleven": 500})
    try:
        with pytest.raises(DailyPasswordError):
            await client.fetch(TODAY)
    finally:
        await client.aclose()


# ---------- 独立 SQLite ----------
def test_store_roundtrip_and_push_dedupe(tmp_path: Path) -> None:
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    snapshot = sample_snapshot()
    try:
        store.save_snapshot(snapshot, fetched_at=123.0)
        assert store.get_snapshot(TODAY) == snapshot
        assert store.get_snapshot("2026-10-22") is None

        assert store.was_pushed(TODAY, 123) is False
        store.mark_pushed(TODAY, 123, pushed_at=456.0)
        assert store.was_pushed(TODAY, 123) is True
        assert store.was_pushed(TODAY, 456) is False

        store.prune_before("2026-10-24")
        assert store.get_snapshot(TODAY) is None
        assert store.was_pushed(TODAY, 123) is False
    finally:
        store.close()


# ---------- 一天一次 / 持久缓存 ----------
async def test_service_fetches_once_and_reuses_persisted_cache(tmp_path: Path) -> None:
    settings = daily_settings()
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    now = datetime(2026, 10, 23, 0, 31, tzinfo=BJ)
    first_client = FakeDailyClient()
    first = DailyPasswordService(settings, first_client, store, now_fn=lambda: now)
    try:
        snapshot = await first.get_today()
        assert snapshot.day == TODAY
        await first.get_today()
        assert first_client.days == [TODAY]

        second_client = FakeDailyClient()
        second = DailyPasswordService(settings, second_client, store, now_fn=lambda: now)
        cached = await second.get_today()
        assert cached == snapshot
        assert second_client.days == []
    finally:
        store.close()


async def test_service_fetches_new_day(tmp_path: Path) -> None:
    settings = daily_settings()
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    clock = {"now": datetime(2026, 10, 23, 0, 31, tzinfo=BJ)}
    client = FakeDailyClient()
    service = DailyPasswordService(settings, client, store, now_fn=lambda: clock["now"])
    try:
        await service.get_today()
        clock["now"] += timedelta(days=1)
        snapshot = await service.get_today()
        assert snapshot.day == "2026-10-24"
        assert client.days == [TODAY, "2026-10-24"]
    finally:
        store.close()


# ---------- 手动查询冷却 ----------
def test_query_cooldown_is_per_group(tmp_path: Path) -> None:
    settings = daily_settings(daily_password_query_cooldown=300)
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    clock = {"now": 0.0}
    service = DailyPasswordService(
        settings,
        FakeDailyClient(),
        store,
        monotonic_fn=lambda: clock["now"],
    )
    try:
        assert service.try_start_query(1) == 0.0
        clock["now"] = 100.0
        assert service.try_start_query(1) == pytest.approx(200.0)
        clock["now"] = 299.0
        assert service.try_start_query(1) == pytest.approx(1.0)
        clock["now"] = 300.0
        assert service.try_start_query(1) == 0.0
        # 其他群独立计算
        assert service.try_start_query(2) == 0.0
        # 被拒绝的尝试不会重置计时器：距离上次成功触发 300 秒即可
        clock["now"] = 301.0
        assert service.try_start_query(2) == pytest.approx(299.0)
    finally:
        store.close()


# ---------- 预热 / 推送窗口 ----------
def test_refresh_window(tmp_path: Path) -> None:
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    service = DailyPasswordService(daily_settings(), FakeDailyClient(), store)
    try:
        assert service.refresh_time_text == "00:05"
        assert service.in_refresh_window(datetime(2026, 10, 23, 0, 4, tzinfo=BJ)) is False
        assert service.in_refresh_window(datetime(2026, 10, 23, 0, 5, tzinfo=BJ)) is True
        assert service.in_refresh_window(datetime(2026, 10, 23, 10, 59, tzinfo=BJ)) is True
        assert service.in_refresh_window(datetime(2026, 10, 23, 11, 0, tzinfo=BJ)) is False
    finally:
        store.close()


def test_push_window(tmp_path: Path) -> None:
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    service = DailyPasswordService(daily_settings(), FakeDailyClient(), store)
    try:
        assert service.push_time_text == "11:00"
        assert service.in_push_window(datetime(2026, 10, 23, 10, 59, tzinfo=BJ)) is False
        assert service.in_push_window(datetime(2026, 10, 23, 11, 0, tzinfo=BJ)) is True
        assert service.in_push_window(datetime(2026, 10, 23, 11, 59, tzinfo=BJ)) is True
        assert service.in_push_window(datetime(2026, 10, 23, 12, 0, tzinfo=BJ)) is False
        # 服务器按 UTC 传进来也能正确换算
        assert service.in_push_window(
            datetime(2026, 10, 23, 3, 0, tzinfo=timezone.utc)
        ) is True
    finally:
        store.close()


def test_invalid_push_time_falls_back(tmp_path: Path) -> None:
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    service = DailyPasswordService(
        daily_settings(daily_password_push_time="25:99"),
        FakeDailyClient(),
        store,
    )
    try:
        assert service.push_time_text == "11:00"
    finally:
        store.close()


def test_invalid_refresh_time_falls_back(tmp_path: Path) -> None:
    store = DailyPasswordStore(tmp_path / "daily_password.sqlite3")
    service = DailyPasswordService(
        daily_settings(daily_password_refresh_time="00:99"),
        FakeDailyClient(),
        store,
    )
    try:
        assert service.refresh_time_text == "00:05"
    finally:
        store.close()


# ---------- 配置 / 排版 ----------
def test_settings_group_fallback_and_sources() -> None:
    settings = daily_settings(chat_groups="100,200", daily_password_groups="")
    assert settings.daily_password_group_set == {100, 200}

    explicit = daily_settings(chat_groups="100", daily_password_groups="300，400")
    assert explicit.daily_password_group_set == {300, 400}

    sources = daily_settings(daily_password_sources="4399, eleven,4399")
    assert sources.daily_password_source_list == ["4399", "eleven"]


def test_format_daily_password() -> None:
    snapshot = sample_snapshot()
    text = format_daily_password(snapshot)

    assert "【三角洲行动 · 每日密码】" in text
    assert "2026-10-23 ｜ 来源：4399游戏盒 ｜ 00:00 更新" in text
    assert "零号大坝：0396" in text
    assert "位置：主变电站右侧" in text
    assert "长弓溪谷：4893" in text
    assert "共 2 张地图" in text
    assert "以游戏内为准" in text


def test_format_wait() -> None:
    assert format_wait(0) == "1 秒"
    assert format_wait(45) == "45 秒"
    assert format_wait(300) == "5 分 0 秒"
    assert format_wait(299.4) == "5 分 0 秒"
