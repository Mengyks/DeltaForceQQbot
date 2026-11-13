"""猪结婚插件：命令规则、群成员归一化、引用回复与错误降级。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.models.waifu import WaifuMember, WaifuResult
from deltaforce_bot.services.waifu import (
    WaifuAvatarDownloadError,
    WaifuNoCandidates,
    WaifuUnavailable,
)
from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "waifu.py"


def make_event(
    text: str,
    *,
    group_id: int = 30000,
    user_id: int = 20000,
    self_id: int = 10000,
    message_id: int = 42,
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=self_id,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=message_id,
        user_id=user_id,
        message=Message(text),
        raw_message=text,
        font=0,
        sender={"card": "群友A", "nickname": "A"},
        group_id=group_id,
    )


@pytest.fixture(scope="module")
def plugin():
    loaded = nonebot.get_plugin("waifu")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "猪结婚插件加载失败"
    return loaded


def test_matcher_priority(plugin) -> None:
    assert plugin.module.waifu_matcher.priority == 10
    assert plugin.module.waifu_matcher.block is True


def test_command_rule(plugin, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin.module,
        "get_runtime",
        lambda: SimpleNamespace(settings=SimpleNamespace(fun_group_set={30000})),
    )
    rule = plugin.module._is_waifu_command

    assert rule(make_event("/waifu")) is True
    assert rule(make_event("/Waifu")) is True
    assert rule(make_event("／waifu")) is True
    assert rule(make_event("  /waifu  ")) is True
    assert rule(make_event("/每天日尊挑战")) is True

    assert rule(make_event("waifu")) is False
    assert rule(make_event("/waifu 123")) is False
    assert rule(make_event("/每天日尊挑战 123")) is False
    assert rule(make_event("/帮助")) is False
    assert rule(make_event("/waifu", group_id=30001)) is False


def test_normalize_members(plugin) -> None:
    members = plugin.module._normalize_members(
        [
            {"user_id": 2, "card": "群友B", "nickname": "B"},
            {"user_id": 3, "nickname": "C"},
            {"user_id": 10000, "nickname": "bot"},
            {"user_id": 4, "nickname": "D", "is_robot": True},
            {"user_id": 2, "nickname": "重复"},
            {"user_id": 0, "nickname": "无效"},
        ],
        self_id=10000,
        ignore_ids={"5"},
    )

    assert {member.user_id for member in members} == {2, 3, 10000, 4}
    by_id = {member.user_id: member for member in members}
    assert by_id[2].name == "群友B"
    assert by_id[3].name == "C"
    assert by_id[10000].is_robot is True
    assert by_id[4].is_robot is True


class StopCommand(Exception):
    pass


class FakeMatcher:
    def __init__(self, *, fail_first_send: bool = False) -> None:
        self.fail_first_send = fail_first_send
        self.send_calls = 0
        self.sent: list[Message] = []
        self.finished: list[str] = []

    async def send(self, message: Message) -> None:
        self.send_calls += 1
        if self.fail_first_send and self.send_calls == 1:
            raise RuntimeError("send failed")
        self.sent.append(message)

    async def finish(self, message: str) -> None:
        self.finished.append(str(message))
        raise StopCommand


class FakeBot:
    def __init__(self, members: list[dict] | None = None, error: Exception | None = None):
        self.members = members or []
        self.error = error
        self.calls: list[int] = []

    async def get_group_member_list(self, *, group_id: int) -> list[dict]:
        self.calls.append(group_id)
        if self.error is not None:
            raise self.error
        return self.members


class FakeService:
    def __init__(
        self,
        *,
        result: WaifuResult | None = None,
        error: Exception | None = None,
        available: bool = True,
    ) -> None:
        self.result = result
        self.error = error
        self.available = available
        self.calls: list[dict] = []

    async def handle(self, **kwargs) -> WaifuResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_runtime(service: FakeService | None, *, ignore: set[str] | None = None):
    return SimpleNamespace(
        settings=SimpleNamespace(chat_ignore_qq_set=ignore or set()),
        waifu=service,
    )


async def call_handler(
    plugin,
    monkeypatch,
    event: GroupMessageEvent,
    *,
    bot: FakeBot | None = None,
    service: FakeService | None = None,
    ignore: set[str] | None = None,
    fail_first_send: bool = False,
):
    matcher = FakeMatcher(fail_first_send=fail_first_send)
    monkeypatch.setattr(plugin.module, "waifu_matcher", matcher)
    monkeypatch.setattr(
        plugin.module, "get_runtime", lambda: make_runtime(service, ignore=ignore)
    )
    try:
        await plugin.module.handle_waifu(bot or FakeBot(), event)
    except StopCommand:
        pass
    return matcher


async def test_handler_sends_quote_image_text(plugin, monkeypatch) -> None:
    result = WaifuResult(partner_id=2, partner_name="群友B", image=b"image-bytes")
    service = FakeService(result=result)
    bot = FakeBot(
        members=[
            {"user_id": 2, "card": "群友B", "nickname": "B"},
            {"user_id": 3, "nickname": "C"},
        ]
    )

    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), bot=bot, service=service
    )

    assert len(matcher.sent) == 1
    segments = matcher.sent[0]
    assert [segment.type for segment in segments] == ["reply", "image", "text"]
    encoded = str(segments[1].data["file"]).removeprefix("base64://")
    assert base64.b64decode(encoded) == b"image-bytes"
    assert segments[2].data["text"] == "你今天的老婆是群友B"

    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["group_id"] == 30000
    assert call["sender_id"] == 20000
    assert call["sender_name"] == "群友A"
    assert [member.user_id for member in call["members"]] == [2, 3]


async def test_handler_no_candidates(plugin, monkeypatch) -> None:
    service = FakeService(error=WaifuNoCandidates("none"))
    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), service=service
    )

    assert matcher.sent == []
    assert "没有其他群友可选" in matcher.finished[-1]


async def test_handler_member_list_failure(plugin, monkeypatch) -> None:
    service = FakeService(result=WaifuResult(2, "B", b"x"))
    bot = FakeBot(error=RuntimeError("api down"))
    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), bot=bot, service=service
    )

    assert matcher.sent == []
    assert "获取群成员失败" in matcher.finished[-1]
    assert service.calls == []


async def test_handler_unavailable(plugin, monkeypatch) -> None:
    service = FakeService(available=False)
    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), service=service
    )

    assert matcher.sent == []
    assert "还没准备好" in matcher.finished[-1]


async def test_handler_avatar_failure(plugin, monkeypatch) -> None:
    service = FakeService(error=WaifuAvatarDownloadError("avatar down"))
    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), service=service
    )

    assert matcher.sent == []
    assert "没拼好" in matcher.finished[-1]


async def test_handler_unavailable_error(plugin, monkeypatch) -> None:
    service = FakeService(error=WaifuUnavailable("no template"))
    matcher = await call_handler(
        plugin, monkeypatch, make_event("/waifu"), service=service
    )

    assert matcher.sent == []
    assert "还没准备好" in matcher.finished[-1]


async def test_handler_ignores_bot_sender(plugin, monkeypatch) -> None:
    service = FakeService(result=WaifuResult(2, "B", b"x"))
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/waifu", user_id=10000),
        service=service,
    )

    assert matcher.sent == []
    assert service.calls == []


async def test_handler_ignores_configured_bot(plugin, monkeypatch) -> None:
    service = FakeService(result=WaifuResult(2, "B", b"x"))
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/waifu", user_id=20000),
        service=service,
        ignore={"20000"},
    )

    assert matcher.sent == []
    assert service.calls == []


async def test_handler_send_failure_falls_back_without_reply(plugin, monkeypatch) -> None:
    result = WaifuResult(partner_id=2, partner_name="群友B", image=b"image-bytes")
    service = FakeService(result=result)
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/waifu"),
        service=service,
        fail_first_send=True,
    )

    assert len(matcher.sent) == 1
    assert [segment.type for segment in matcher.sent[0]] == ["image", "text"]


def test_build_message_without_reply_when_message_id_missing(plugin) -> None:
    result = WaifuResult(partner_id=2, partner_name="群友B", image=b"image-bytes")
    event = make_event("/waifu", message_id=0)

    message = plugin.module._build_message(event, result)

    assert [segment.type for segment in message] == ["image", "text"]
