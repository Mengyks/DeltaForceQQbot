"""帮助插件：命令规则、图片发送、缓存与文字兜底。"""

from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "help.py"


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
    loaded = nonebot.get_plugin("help")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "帮助插件加载失败"
    return loaded


@pytest.fixture(autouse=True)
def _clear_cache(plugin):
    plugin.module._image_cache.clear()


def test_matcher_priority(plugin) -> None:
    assert plugin.module.help_matcher.priority == 10
    assert plugin.module.help_matcher.block is True


def test_command_rule(plugin) -> None:
    rule = plugin.module._is_help_command

    assert rule(make_event("/帮助")) is True
    assert rule(make_event("／帮助")) is True
    assert rule(make_event("/help")) is True
    assert rule(make_event("/帮助命令")) is True
    assert rule(make_event("/功能")) is True
    assert rule(make_event("帮助")) is False
    assert rule(make_event("#帮助")) is False
    assert rule(make_event("今天有什么帮助")) is False


class StopCommand(Exception):
    pass


class FakeMatcher:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.sent: list[Message] = []
        self.finished: list[str] = []

    async def send(self, message: Message) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(message)

    async def finish(self, message: str) -> None:
        self.finished.append(str(message))
        raise StopCommand


def make_runtime(tmp_path):
    return SimpleNamespace(
        settings=SimpleNamespace(assets_dir=tmp_path / "items"),
        renderer=object(),
    )


async def call_handler(plugin, monkeypatch, event, runtime, *, render=b"image"):
    matcher = FakeMatcher(fail_send=getattr(runtime, "fail_send", False))
    monkeypatch.setattr(plugin.module, "help_matcher", matcher)
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: runtime)

    async def fake_render(renderer, context, **kwargs):
        return render

    monkeypatch.setattr(plugin.module, "render_help_card", fake_render)
    try:
        await plugin.module.handle_help(event)
    except StopCommand:
        pass
    return matcher


async def test_handler_sends_image_and_caches(plugin, monkeypatch, tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/帮助"),
        runtime,
        render=b"help-image",
    )

    assert len(matcher.sent) == 1
    assert matcher.sent[0][0].type == "image"
    assert matcher.finished == []
    assert len(plugin.module._image_cache) == 1

    # 第二次命中缓存，不再渲染
    async def fail_render(renderer, context, **kwargs):
        raise AssertionError("缓存命中时不应重新渲染")

    matcher2 = FakeMatcher()
    monkeypatch.setattr(plugin.module, "help_matcher", matcher2)
    monkeypatch.setattr(plugin.module, "render_help_card", fail_render)
    await plugin.module.handle_help(make_event("/帮助"))
    assert len(matcher2.sent) == 1
    assert matcher2.finished == []


async def test_handler_falls_back_to_text(plugin, monkeypatch, tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/帮助"),
        runtime,
        render=None,
    )
    assert matcher.sent == []
    assert matcher.finished
    assert "【五点老师 · 帮助命令】" in matcher.finished[-1]
    assert "/卡战备" in matcher.finished[-1]


async def test_handler_send_failure_falls_back(plugin, monkeypatch, tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.fail_send = True
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/帮助"),
        runtime,
        render=b"help-image",
    )
    assert matcher.sent == []
    assert matcher.finished
    assert "【五点老师 · 帮助命令】" in matcher.finished[-1]
