"""直播订阅插件：命令规则、权限判断、卡片/文字推送与兜底。"""

from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter
from deltaforce_bot.models.live import LiveNotification, LiveStatus
from deltaforce_bot.services.text import parse_live_command
from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "live_watch.py"


def make_event(text: str, *, role: str = "", group_id: int = 30000) -> GroupMessageEvent:
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
        sender={"role": role} if role else {},
        group_id=group_id,
    )


def make_notification(group_id: int = 30000, *, uid: str = "1") -> LiveNotification:
    return LiveNotification(
        uid=uid,
        group_id=group_id,
        session_key="1792912598",
        uname="测试主播",
        room_id="1883376823",
        title="直播标题",
        cover="https://example.test/cover.jpg",
        live_time=1792912598,
    )


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def plugin():
    loaded = nonebot.get_plugin("live_watch")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "直播订阅插件加载失败"
    return loaded


def test_matcher_priority(plugin) -> None:
    assert plugin.module.live_matcher.priority == 10
    assert plugin.module.live_matcher.block is True


def test_command_rule_strict_slash(plugin) -> None:
    rule = plugin.module._is_live_command

    assert rule(make_event("/直播订阅 添加 672328094")) is True
    assert rule(make_event("／直播订阅 列表")) is True
    assert rule(make_event("  /直播订阅 查询 1  ")) is True
    assert rule(make_event("直播订阅 列表")) is False
    assert rule(make_event("#直播订阅 列表")) is False
    assert rule(make_event("今天直播订阅了吗")) is False


def test_parse_live_command() -> None:
    assert parse_live_command("/直播订阅").action == "help"
    assert parse_live_command("/直播订阅 添加 672328094").action == "add"
    assert parse_live_command("/直播订阅 添加 672328094").uid == "672328094"
    assert parse_live_command("/直播订阅 删除 1").action == "remove"
    assert parse_live_command("/直播订阅 列表").action == "list"
    assert parse_live_command("/直播订阅 查询 1").action == "query"
    assert parse_live_command("/直播订阅 订阅 1").action == "add"
    assert parse_live_command("/直播订阅 取消 1").action == "remove"
    assert parse_live_command("/直播订阅 添加").error is not None
    assert parse_live_command("/直播订阅 列表 多余").error is not None
    assert parse_live_command("/直播订阅 嗯").error is not None
    assert parse_live_command("/卡战备 巴克什 绝密") is None


def test_group_owner_only(plugin) -> None:
    assert plugin.module._is_group_owner(make_event("x", role="owner")) is True
    assert plugin.module._is_group_owner(make_event("x", role="admin")) is False
    assert plugin.module._is_group_owner(make_event("x", role="member")) is False


class StopCommand(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def finish(self, message: str) -> None:
        self.messages.append(str(message))
        raise StopCommand


class FakeCommandService:
    def __init__(self) -> None:
        self.added: list[tuple[str, int]] = []
        self.removed: list[str] = []
        self.queried: list[str] = []
        self.list_calls = 0

    async def add(self, uid: str, *, user_id: int = 0):
        self.added.append((uid, user_id))
        return SimpleNamespace(message="ADDED")

    async def remove(self, uid: str):
        self.removed.append(uid)
        return SimpleNamespace(message="REMOVED")

    async def list_subscriptions(self):
        self.list_calls += 1
        return []

    async def query(self, uid: str):
        self.queried.append(uid)
        return LiveStatus(
            uid=uid,
            room_id="10",
            uname="主播",
            live_status=1,
            live_time=1000,
            title="标题",
        )


def make_command_runtime(service: FakeCommandService | None = None):
    limiter = SlidingWindowLimiter(100)
    return SimpleNamespace(
        live=service or FakeCommandService(),
        settings=SimpleNamespace(chat_group_set={30000}),
        is_debug_group=lambda group_id: True,
        live_group_limiter=limiter,
        live_user_limiter=limiter,
    )


async def call_command(
    plugin,
    monkeypatch,
    event: GroupMessageEvent,
    *,
    service: FakeCommandService | None = None,
    runtime=None,
) -> tuple[FakeMatcher, FakeCommandService]:
    matcher = FakeMatcher()
    service = service or FakeCommandService()
    runtime = runtime or make_command_runtime(service)
    monkeypatch.setattr(plugin.module, "live_matcher", matcher)
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: runtime)
    try:
        await plugin.module.handle_live_command(event)
    except StopCommand:
        pass
    return matcher, service


async def test_command_permission_and_actions(plugin, monkeypatch) -> None:
    service = FakeCommandService()

    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 添加 1", role="admin"),
        service=service,
    )
    assert "只有群主或 SUPERUSERS" in matcher.messages[-1]
    assert service.added == []

    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 添加 1", role="owner"),
        service=service,
    )
    assert service.added == [("1", 20000)]
    assert matcher.messages[-1] == "ADDED"

    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 列表", role="member"),
        service=service,
    )
    assert service.list_calls == 1
    assert "当前没有直播订阅" in matcher.messages[-1]

    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 查询 1", role="member"),
        service=service,
    )
    assert service.queried == ["1"]
    assert "直播中" in matcher.messages[-1]
    assert "https://live.bilibili.com/10" in matcher.messages[-1]


async def test_command_superuser_can_manage(plugin, monkeypatch) -> None:
    monkeypatch.setattr(plugin.module, "_is_superuser", lambda event: True)
    service = FakeCommandService()

    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 删除 1", role="member"),
        service=service,
    )
    assert service.removed == ["1"]
    assert matcher.messages[-1] == "REMOVED"


async def test_command_rejects_other_group_and_disabled(plugin, monkeypatch) -> None:
    service = FakeCommandService()
    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 列表", role="owner", group_id=99999),
        service=service,
    )
    assert "本群未配置直播订阅" in matcher.messages[-1]

    disabled = make_command_runtime(service)
    disabled.live = None
    matcher, _ = await call_command(
        plugin,
        monkeypatch,
        make_event("/直播订阅 列表", role="owner"),
        runtime=disabled,
    )
    assert "未启用" in matcher.messages[-1]


class FakeBot:
    def __init__(self, *, fail_image: bool = False) -> None:
        self.fail_image = fail_image
        self.messages: list[tuple[int, Message]] = []

    async def send_group_msg(self, *, group_id: int, message: Message) -> None:
        if self.fail_image and any(segment.type == "image" for segment in message):
            raise RuntimeError("image send failed")
        self.messages.append((group_id, message))


class FakeService:
    def __init__(self) -> None:
        self.notified: set[tuple[str, int, str]] = set()
        self.marked: list[LiveNotification] = []

    async def was_notified(self, notification: LiveNotification) -> bool:
        return (notification.uid, notification.group_id, notification.session_key) in self.notified

    async def mark_notified(self, notification: LiveNotification) -> None:
        self.marked.append(notification)
        self.notified.add((notification.uid, notification.group_id, notification.session_key))


def make_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        renderer=object(),
        live_client=object(),
        settings=SimpleNamespace(live_image_max_kb=350),
    )


async def _deliver_with(plugin, monkeypatch, *, image: bytes | None, fail_image: bool = False, bots=None, notifications=None):
    service = FakeService()
    bot = FakeBot(fail_image=fail_image)
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: bots if bots is not None else {"10000": bot})
    monkeypatch.setattr(plugin.module, "get_runtime", make_runtime)

    async def fake_render(runtime, notification):
        return image

    monkeypatch.setattr(plugin.module, "_render_notification", fake_render)
    items = notifications if notifications is not None else [make_notification(30000), make_notification(30001)]
    await plugin.module._deliver(service, items)
    return service, bot


async def test_deliver_sends_image_plus_text_to_each_group(plugin, monkeypatch) -> None:
    service, bot = await _deliver_with(plugin, monkeypatch, image=b"fake-image")

    assert len(bot.messages) == 2
    assert service.marked and len(service.marked) == 2
    for group_id, message in bot.messages:
        assert group_id in (30000, 30001)
        assert [segment.type for segment in message] == ["image", "text"]
        assert str(message[0].data["file"]).startswith("base64://")
        assert "直播间：https://live.bilibili.com/1883376823" in message[1].data["text"]


async def test_deliver_falls_back_to_text_when_render_fails(plugin, monkeypatch) -> None:
    service, bot = await _deliver_with(plugin, monkeypatch, image=None)

    assert len(bot.messages) == 2
    assert len(service.marked) == 2
    for _, message in bot.messages:
        assert [segment.type for segment in message] == ["text"]
        assert "https://live.bilibili.com/1883376823" in message[0].data["text"]


async def test_deliver_falls_back_to_text_when_image_send_fails(plugin, monkeypatch) -> None:
    service, bot = await _deliver_with(plugin, monkeypatch, image=b"fake-image", fail_image=True)

    assert len(bot.messages) == 2
    assert len(service.marked) == 2
    for _, message in bot.messages:
        assert [segment.type for segment in message] == ["text"]


async def test_deliver_without_bot_does_not_mark(plugin, monkeypatch) -> None:
    service, bot = await _deliver_with(plugin, monkeypatch, image=b"fake-image", bots={})

    assert bot.messages == []
    assert service.marked == []


async def test_deliver_skips_already_notified(plugin, monkeypatch) -> None:
    service = FakeService()
    service.notified.add(("1", 30000, "1792912598"))
    bot = FakeBot()
    monkeypatch.setattr(plugin.module.nonebot, "get_bots", lambda: {"10000": bot})
    monkeypatch.setattr(plugin.module, "get_runtime", make_runtime)

    async def fake_render(runtime, notification):
        return b"fake-image"

    monkeypatch.setattr(plugin.module, "_render_notification", fake_render)
    await plugin.module._deliver(service, [make_notification(30000)])

    assert bot.messages == []
    assert service.marked == []


def test_message_text_contains_title_and_room_url(plugin) -> None:
    notification = make_notification()
    text = plugin.module.format_live_notification_text(notification)
    assert "【B站开播】测试主播" in text
    assert "直播标题" in text
    assert "https://live.bilibili.com/1883376823" in text
