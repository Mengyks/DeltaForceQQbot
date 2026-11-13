"""每日天尊挑战插件：白名单、命令规则、纯图片引用回复与错误降级。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.models.daily_challenge import DailyChallengeResult
from deltaforce_bot.services.daily_challenge import (
    DailyChallengeAvatarError,
    DailyChallengeUnavailable,
)
from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "daily_challenge.py"


def make_event(
    text: str,
    *,
    group_id: int = 30000,
    user_id: int = 20000,
    message_id: int = 42,
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=10000,
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
    loaded = nonebot.get_plugin("daily_challenge")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "每日天尊挑战插件加载失败"
    return loaded


def test_matcher_priority(plugin) -> None:
    assert plugin.module.daily_challenge_matcher.priority == 10
    assert plugin.module.daily_challenge_matcher.block is True


def test_command_rule_requires_whitelist(plugin, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin.module,
        "get_runtime",
        lambda: SimpleNamespace(settings=SimpleNamespace(fun_group_set={30000})),
    )
    rule = plugin.module._is_daily_challenge_command

    assert rule(make_event("/每日天尊挑战")) is True
    assert rule(make_event("／每日天尊挑战")) is True
    assert rule(make_event("  /每日天尊挑战  ")) is True

    assert rule(make_event("每日天尊挑战")) is False
    assert rule(make_event("/每日天尊挑战 123")) is False
    assert rule(make_event("/waifu")) is False
    assert rule(make_event("/每日天尊挑战", group_id=30001)) is False


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


class FakeService:
    def __init__(
        self,
        *,
        result: DailyChallengeResult | None = None,
        error: Exception | None = None,
        available: bool = True,
    ) -> None:
        self.result = result
        self.error = error
        self.available = available
        self.calls: list[dict] = []

    async def handle(self, **kwargs) -> DailyChallengeResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_runtime(service: FakeService | None):
    return SimpleNamespace(
        settings=SimpleNamespace(fun_group_set={30000}),
        daily_challenge=service,
    )


async def call_handler(
    plugin,
    monkeypatch,
    event: GroupMessageEvent,
    *,
    service: FakeService | None = None,
    fail_first_send: bool = False,
):
    matcher = FakeMatcher(fail_first_send=fail_first_send)
    monkeypatch.setattr(plugin.module, "daily_challenge_matcher", matcher)
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: make_runtime(service))
    try:
        await plugin.module.handle_daily_challenge(event)
    except StopCommand:
        pass
    return matcher


async def test_handler_sends_quote_and_image_only(plugin, monkeypatch) -> None:
    result = DailyChallengeResult("灭尽龙", "大剑", b"image-bytes")
    service = FakeService(result=result)

    matcher = await call_handler(
        plugin, monkeypatch, make_event("/每日天尊挑战"), service=service
    )

    assert len(matcher.sent) == 1
    segments = matcher.sent[0]
    assert [segment.type for segment in segments] == ["reply", "image"]
    encoded = str(segments[1].data["file"]).removeprefix("base64://")
    assert base64.b64decode(encoded) == b"image-bytes"

    assert service.calls == [
        {"group_id": 30000, "user_id": 20000, "hunter_name": "群友A"}
    ]


async def test_handler_uses_group_nickname(plugin, monkeypatch) -> None:
    result = DailyChallengeResult("灭尽龙", "大剑", b"image")
    service = FakeService(result=result)
    event = make_event("/每日天尊挑战")
    event.sender.card = ""  # type: ignore[union-attr]
    event.sender.nickname = "昵称A"  # type: ignore[union-attr]

    await call_handler(plugin, monkeypatch, event, service=service)

    assert service.calls[0]["hunter_name"] == "昵称A"


async def test_handler_unavailable(plugin, monkeypatch) -> None:
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/每日天尊挑战"),
        service=FakeService(available=False),
    )

    assert matcher.sent == []
    assert "还没准备好" in matcher.finished[-1]


async def test_handler_service_unavailable_error(plugin, monkeypatch) -> None:
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/每日天尊挑战"),
        service=FakeService(error=DailyChallengeUnavailable("no")),
    )

    assert matcher.sent == []
    assert "还没准备好" in matcher.finished[-1]


async def test_handler_avatar_failure(plugin, monkeypatch) -> None:
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/每日天尊挑战"),
        service=FakeService(error=DailyChallengeAvatarError("avatar")),
    )

    assert matcher.sent == []
    assert "没生成成功" in matcher.finished[-1]


async def test_handler_send_failure_falls_back_to_image(plugin, monkeypatch) -> None:
    result = DailyChallengeResult("灭尽龙", "大剑", b"image-bytes")
    service = FakeService(result=result)

    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/每日天尊挑战"),
        service=service,
        fail_first_send=True,
    )

    assert len(matcher.sent) == 1
    assert [segment.type for segment in matcher.sent[0]] == ["image"]
