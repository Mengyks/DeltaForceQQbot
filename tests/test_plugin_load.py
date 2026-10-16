"""插件加载测试：确认插件能被 NoneBot 加载、规则只匹配本插件的命令。"""

from __future__ import annotations

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "gear_loadout.py"


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
    """插件在一个进程里只能加载一次，已加载则直接复用。"""
    loaded = nonebot.get_plugin("gear_loadout")
    if loaded is not None:
        return loaded
    plugin = nonebot.load_plugin(PLUGIN_PATH)
    assert plugin is not None, "插件加载失败"
    return plugin


def test_plugin_loads_and_registers_matcher(plugin) -> None:
    matcher = plugin.module.gear_matcher

    assert matcher.priority == 10
    assert matcher.block is True


def test_rule_matches_only_own_commands(plugin) -> None:
    rule = plugin.module._is_gear_command

    assert rule(make_event("卡战备 11")) is True
    assert rule(make_event("#卡战备 55 兑换")) is True
    assert rule(make_event("凑战备 5万")) is True
    assert rule(make_event("  凑战备 50000 不要枪")) is True
    assert rule(make_event("今天天气真好")) is False
    assert rule(make_event("我说卡战备很强")) is False
