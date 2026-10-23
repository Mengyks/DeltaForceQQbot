"""每日密码插件：命令规则、matcher 优先级与定时推送。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.adapters.cache import TTLCache
from deltaforce_bot.adapters.daily_password import DailyPasswordError
from deltaforce_bot.adapters.daily_password_store import DailyPasswordStore
from deltaforce_bot.models.daily_password import DailyPasswordEntry, DailyPasswordSnapshot
from deltaforce_bot.services.daily_password import DailyPasswordService
from tests.support import PROJECT_ROOT, make_settings

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "daily_password.py"
BJ = timezone(timedelta(hours=8))
TODAY = "2026-10-23"


def make_event(text: str) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=10000,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=20000,
        message=Message(text),
        raw_message=text,
        font=0,
        sender={},
        group_id=30000,
    )


@pytest.fixture(scope="module")
def plugin():
    loaded = nonebot.get_plugin("daily_password")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "每日密码插件加载失败"
    return loaded


@pytest.fixture(autouse=True)
def _no_image_runtime(plugin, monkeypatch):
    """推送测试默认走文字路径，避免测试进程启动浏览器。"""
    monkeypatch.setattr(
        plugin.module,
        "get_runtime",
        lambda: SimpleNamespace(
            renderer=None,
            assets=None,
            daily_password_image_cache=None,
        ),
    )


def test_matcher_priority(plugin) -> None:
    assert plugin.module.daily_password_matcher.priority == 10
    assert plugin.module.daily_password_matcher.block is True


def test_command_rule(plugin) -> None:
    rule = plugin.module._is_daily_password_command

    assert rule(make_event("/每日密码")) is True
    assert rule(make_event("每日密码")) is True
    assert rule(make_event("#每日密码 查询")) is True
    assert rule(make_event("今日密码")) is True
    assert rule(make_event("  /今日密码 查询  ")) is True

    assert rule(make_event("每日密码是多少")) is False
    assert rule(make_event("今天每日密码")) is False
    assert rule(make_event("卡战备 巴克什 绝密")) is False
    assert rule(make_event("今天天气真好")) is False


class FakeBot:
    def __init__(self, fail_groups: set[int] | None = None) -> None:
        self.fail_groups = set(fail_groups or ())
        self.messages: list[tuple[int, str]] = []

    async def send_group_msg(self, *, group_id: int, message: str) -> None:
        if group_id in self.fail_groups:
            raise RuntimeError("send failed")
        self.messages.append((group_id, message))


class PushClient:
    def __init__(self) -> None:
        self.days: list[str] = []

    async def fetch(self, day: str) -> DailyPasswordSnapshot:
        self.days.append(day)
        return DailyPasswordSnapshot(
            day=day,
            source="test",
            updated_at=f"{day}T00:00:00",
            entries=(DailyPasswordEntry(name="零号大坝", code="0396"),),
        )

    async def aclose(self) -> None:
        pass


class FailingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, day: str) -> DailyPasswordSnapshot:
        self.calls += 1
        raise DailyPasswordError("source down")

    async def aclose(self) -> None:
        pass


def make_push_service(tmp_path: Path, hour: int = 11, minute: int = 1, client=None):
    settings = make_settings(
        daily_password_enabled=True,
        daily_password_sources="4399",
        daily_password_db_path=tmp_path / "daily_password.sqlite3",
        daily_password_refresh_time="00:05",
        daily_password_push_time="11:00",
        daily_password_push_window_minutes=60,
        daily_password_query_cooldown=300,
        daily_password_tz_offset=8,
    )
    store = DailyPasswordStore(settings.daily_password_db_path)
    client = client or PushClient()
    now = datetime(2026, 10, 23, hour, minute, tzinfo=BJ)
    service = DailyPasswordService(settings, client, store, now_fn=lambda: now)
    return service, client, store


def reset_push_globals(plugin) -> None:
    plugin.module._push_error_logged.clear()
    plugin.module._push_error_day = ""
    plugin.module._last_push_fetch_attempt = 0.0


class StubAssets:
    def background(self, name: str):
        return None


class FakeRenderer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def render_template(self, context, *, template_name: str, **kwargs) -> bytes:
        self.calls += 1
        assert template_name == "daily_password.html.j2"
        assert kwargs.get("screenshot_type") == "jpeg"
        if self.fail:
            raise RuntimeError("render failed")
        return b"png-bytes"


def make_image_runtime(renderer: FakeRenderer):
    return SimpleNamespace(
        renderer=renderer,
        assets=StubAssets(),
        daily_password_image_cache=TTLCache(),
    )


def make_image_snapshot() -> DailyPasswordSnapshot:
    return DailyPasswordSnapshot(
        day=TODAY,
        source="test",
        updated_at=f"{TODAY}T00:00:00",
        entries=(DailyPasswordEntry(name="零号大坝", code="0396"),),
    )


async def test_render_image_is_cached(plugin) -> None:
    renderer = FakeRenderer()
    runtime = make_image_runtime(renderer)
    snapshot = make_image_snapshot()

    assert await plugin.module._render_daily_password_image(runtime, snapshot) == b"png-bytes"
    assert await plugin.module._render_daily_password_image(runtime, snapshot) == b"png-bytes"
    assert renderer.calls == 1


async def test_render_image_failure_falls_back(plugin) -> None:
    renderer = FakeRenderer(fail=True)
    runtime = make_image_runtime(renderer)

    assert await plugin.module._render_daily_password_image(runtime, make_image_snapshot()) is None
    assert renderer.calls == 1


async def test_refresh_if_needed_warms_cache_once(plugin, tmp_path: Path) -> None:
    service, client, store = make_push_service(tmp_path, hour=0, minute=10)
    state = plugin.module._RefreshState()
    try:
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=0.0) is True
        assert client.days == [TODAY]
        assert await service.has_today_snapshot() is True

        # 已有缓存：后续检查不再取数
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=30.0) is False
        assert client.days == [TODAY]
    finally:
        store.close()


async def test_refresh_retries_then_stops(plugin, tmp_path: Path) -> None:
    client = FailingClient()
    service, _client, store = make_push_service(
        tmp_path,
        hour=0,
        minute=10,
        client=client,
    )
    state = plugin.module._RefreshState()
    try:
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=0.0) is False
        assert client.calls == 1

        # 60 秒内不重复请求
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=30.0) is False
        assert client.calls == 1

        # 到 60 秒重试一次
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=60.0) is False
        assert client.calls == 2

        # 超过 15 分钟重试窗口后停止自动重试
        assert await plugin.module._refresh_if_needed(service, state, now_monotonic=901.0) is False
        assert client.calls == 2
    finally:
        store.close()


async def test_push_once_sends_and_dedupes(plugin, tmp_path: Path, monkeypatch) -> None:
    service, client, store = make_push_service(tmp_path)
    bot = FakeBot()
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {"10000": bot})
    reset_push_globals(plugin)
    try:
        await plugin.module._push_once(service, {1, 2})
        assert sorted(group for group, _ in bot.messages) == [1, 2]
        assert "零号大坝：0396" in bot.messages[0][1]
        assert client.days == [TODAY]
        assert await service.was_pushed(TODAY, 1) is True
        assert await service.was_pushed(TODAY, 2) is True

        # 同一天再跑一次：不重复推送
        await plugin.module._push_once(service, {1, 2})
        assert len(bot.messages) == 2
    finally:
        store.close()


async def test_push_once_sends_image_when_rendering_works(
    plugin,
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _client, store = make_push_service(tmp_path)
    bot = FakeBot()
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {"10000": bot})

    renderer = FakeRenderer()
    runtime = make_image_runtime(renderer)
    runtime.settings = SimpleNamespace(daily_password_push_time="11:00")
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: runtime)

    reset_push_globals(plugin)
    try:
        await plugin.module._push_once(service, {1})
        assert len(bot.messages) == 1
        message = bot.messages[0][1]
        assert isinstance(message, Message)
        assert message[0].type == "image"
        assert str(message[0].data["file"]).startswith("base64://")
        assert await service.was_pushed(TODAY, 1) is True
    finally:
        store.close()


async def test_push_outside_window_does_nothing(plugin, tmp_path: Path, monkeypatch) -> None:
    service, client, store = make_push_service(tmp_path, hour=10, minute=59)
    bot = FakeBot()
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {"10000": bot})
    reset_push_globals(plugin)
    try:
        await plugin.module._push_once(service, {1})
        assert bot.messages == []
        assert client.days == []
    finally:
        store.close()


async def test_push_without_bot_waits(plugin, tmp_path: Path, monkeypatch) -> None:
    service, client, store = make_push_service(tmp_path)
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {})
    reset_push_globals(plugin)
    try:
        await plugin.module._push_once(service, {1})
        assert client.days == []
        assert await service.was_pushed(TODAY, 1) is False
    finally:
        store.close()


async def test_push_failed_group_retries(plugin, tmp_path: Path, monkeypatch) -> None:
    service, _client, store = make_push_service(tmp_path)
    bot = FakeBot(fail_groups={2})
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {"10000": bot})
    reset_push_globals(plugin)
    try:
        await plugin.module._push_once(service, {1, 2})
        assert [group for group, _ in bot.messages] == [1]
        assert await service.was_pushed(TODAY, 1) is True
        assert await service.was_pushed(TODAY, 2) is False

        bot.fail_groups.clear()
        await plugin.module._push_once(service, {1, 2})
        assert [group for group, _ in bot.messages] == [1, 2]
        assert await service.was_pushed(TODAY, 2) is True
    finally:
        store.close()
