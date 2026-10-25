"""制造台利润插件：命令规则、图片卡片、文字兜底与异常。"""

from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.adapters.cache import TTLCache
from deltaforce_bot.models.crafting import (
    STATIONS,
    CraftingItem,
    CraftingResult,
    CraftingStationResult,
)
from deltaforce_bot.services.crafting import CraftingUnavailable
from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "crafting.py"


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


def make_result() -> CraftingResult:
    items = [
        CraftingItem(name="技术物品", grade=4, period=4, price=1000, price_hour=250),
        CraftingItem(name="工作台物品", grade=5, period=4, price=2000, price_hour=500),
        CraftingItem(name="制药物品", grade=6, period=4, price=3000, price_hour=750),
        CraftingItem(name="防具物品", grade=4, period=4, price=4000, price_hour=1000),
    ]
    return CraftingResult(
        tuple(
            CraftingStationResult(station_id, label, item)
            for (station_id, label), item in zip(STATIONS, items, strict=True)
        )
    )


@pytest.fixture(scope="module")
def plugin():
    loaded = nonebot.get_plugin("crafting")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "制造台利润插件加载失败"
    return loaded


def test_matcher_priority(plugin) -> None:
    assert plugin.module.crafting_matcher.priority == 10
    assert plugin.module.crafting_matcher.block is True


def test_command_rule(plugin) -> None:
    rule = plugin.module._is_crafting_command

    assert rule(make_event("/制造利润")) is True
    assert rule(make_event("／制造台利润")) is True
    assert rule(make_event("/每日利润")) is True
    assert rule(make_event("制造利润")) is False
    assert rule(make_event("/制造利润 多余")) is True
    assert rule(make_event("/卡战备 巴克什 绝密")) is False


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


class FakeService:
    def __init__(self, result: CraftingResult | None = None, error: Exception | None = None) -> None:
        self.result = result or make_result()
        self.error = error
        self.calls = 0

    async def get_profit(self) -> CraftingResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def make_runtime(service: FakeService | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        crafting=service,
        crafting_assets=SimpleNamespace(find=lambda *args: None),
        crafting_image_cache=TTLCache(),
        renderer=object(),
        settings=SimpleNamespace(crafting_cache_ttl=43200, crafting_image_max_kb=350),
    )


async def call_handler(plugin, monkeypatch, event, runtime, *, render=None):
    matcher = FakeMatcher(fail_send=getattr(runtime, "fail_send", False))
    monkeypatch.setattr(plugin.module, "crafting_matcher", matcher)
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: runtime)
    if render is not None:
        async def fake_render(renderer, context, **kwargs):
            return render
        monkeypatch.setattr(plugin.module, "render_crafting_card", fake_render)
    try:
        await plugin.module.handle_crafting(event)
    except StopCommand:
        pass
    return matcher


async def test_handler_sends_image_and_caches(plugin, monkeypatch) -> None:
    service = FakeService()
    runtime = make_runtime(service)
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/制造利润"),
        runtime,
        render=b"fake-image",
    )

    assert service.calls == 1
    assert matcher.finished == []
    assert len(matcher.sent) == 1
    assert matcher.sent[0][0].type == "image"
    assert len(runtime.crafting_image_cache) == 1

    # 第二次直接命中图片缓存，不再渲染
    async def fail_render(renderer, context, **kwargs):
        raise AssertionError("缓存命中时不应重新渲染")

    matcher2 = FakeMatcher()
    monkeypatch.setattr(plugin.module, "crafting_matcher", matcher2)
    monkeypatch.setattr(plugin.module, "render_crafting_card", fail_render)
    await plugin.module.handle_crafting(make_event("/制造利润"))
    assert matcher2.finished == []
    assert len(matcher2.sent) == 1


async def test_handler_falls_back_to_text(plugin, monkeypatch) -> None:
    runtime = make_runtime(FakeService())
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/制造利润"),
        runtime,
        render=None,
    )
    assert matcher.sent == []
    assert matcher.finished and "【每日利润 · Lv3】" in matcher.finished[-1]


async def test_handler_send_failure_falls_back(plugin, monkeypatch) -> None:
    runtime = make_runtime(FakeService())
    runtime.fail_send = True
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/制造利润"),
        runtime,
        render=b"fake-image",
    )
    assert matcher.sent == []
    assert matcher.finished and "【每日利润 · Lv3】" in matcher.finished[-1]


async def test_handler_unavailable_and_disabled(plugin, monkeypatch) -> None:
    runtime = make_runtime(FakeService(error=CraftingUnavailable("数据源不可用")))
    matcher = await call_handler(plugin, monkeypatch, make_event("/制造利润"), runtime, render=b"x")
    assert matcher.finished[-1] == "数据源不可用"

    runtime = make_runtime(None)
    matcher = await call_handler(plugin, monkeypatch, make_event("/制造利润"), runtime, render=b"x")
    assert "未启用" in matcher.finished[-1]


async def test_handler_extra_args_shows_help(plugin, monkeypatch) -> None:
    service = FakeService()
    runtime = make_runtime(service)
    matcher = await call_handler(
        plugin,
        monkeypatch,
        make_event("/制造利润 多余"),
        runtime,
        render=b"x",
    )
    assert service.calls == 0
    assert matcher.finished and "用法" in matcher.finished[-1]
