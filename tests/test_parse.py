"""命令解析测试：地图+难度、兑换选项、错误分支。"""

from __future__ import annotations

import pytest

from deltaforce_bot.services.text import (
    CUSTOM_DISABLED_NOTE,
    format_help,
    parse,
    parse_amount,
)
from tests.support import load_gates

GATES = load_gates()


@pytest.mark.parametrize(
    ("text", "lv"),
    [
        ("卡战备 巴克什 绝密", 2),
        ("#卡战备 巴克什 绝密", 2),
        ("/卡战备 巴克什 绝密", 2),
        ("卡战备 集市 绝密", 2),
        ("卡战备 航天 绝密", 3),
        ("卡战备 航天基地 绝密", 3),
        ("卡战备 大坝 机密", 0),
        ("卡战备 零号大坝 机密", 0),
        ("卡战备 长弓 机密", 0),
        ("卡战备 长弓溪谷 机密", 0),
        ("卡战备 巴克什 机密", 1),
        ("卡战备 航天 机密", 1),
        ("卡战备 潮汐监狱 绝密", 5),
        ("卡战备 潮汐 绝密", 5),
        ("卡战备 监狱 绝密", 5),
        ("卡战备 监狱", 5),  # 只有一种难度时自动选中
    ],
)
def test_gate_resolution(text: str, lv: int) -> None:
    query = parse(text, GATES)
    assert query is not None
    assert query.kind == "gate"
    assert query.gate is not None and query.gate.lv == lv


def test_exchange_option() -> None:
    query = parse("卡战备 巴克什 绝密 兑换", GATES)
    assert query.kind == "gate" and query.include_exchange is True

    query = parse("卡战备 巴克什 绝密", GATES)
    assert query.include_exchange is False


@pytest.mark.parametrize("text", ["卡战备 55", "卡战备 60", "卡战备 11", "卡战备 78"])
def test_numeric_input_is_rejected(text: str) -> None:
    """命令只接受「地图 + 难度」，数字档位不再作为快捷写法（按用户要求移除）。"""
    query = parse(text, GATES)
    assert query.kind == "help"
    assert "不认识地图" in query.error
    assert "可查档位" in format_help(GATES)  # 错误提示外还有菜单可看


def test_multiple_difficulties_require_explicit_choice() -> None:
    query = parse("卡战备 巴克什", GATES)
    assert query.kind == "help"
    assert "需要指定难度" in query.error
    assert "机密" in query.error and "绝密" in query.error


def test_regular_difficulty_is_not_supported() -> None:
    for text in ("卡战备 巴克什 常规", "卡战备 大坝 常规"):
        query = parse(text, GATES)
        assert query.kind == "help"
        assert "常规模式不需要战备值" in query.error


def test_difficulty_not_available_on_map() -> None:
    query = parse("卡战备 大坝 绝密", GATES)
    assert query.kind == "help"
    assert "零号大坝没有绝密难度" in query.error


def test_unknown_map_and_difficulty() -> None:
    query = parse("卡战备 海边 绝密", GATES)
    assert query.kind == "help"
    assert "不认识地图" in query.error

    query = parse("卡战备 巴克什 地狱", GATES)
    assert query.kind == "help"
    assert "不认识难度" in query.error


def test_missing_arguments() -> None:
    query = parse("卡战备", GATES)
    assert query.kind == "help"
    assert "请给出地图和难度" in query.error

    query = parse("卡战备 布拉 绝密 兑换 多余", GATES)
    assert query.kind == "help"
    assert "参数多了" in query.error


def test_fullwidth_and_alias_tolerance() -> None:
    query = parse("卡战备 5 5", GATES)  # 无关数字视为地图名
    assert query.kind == "help"

    query = parse("卡战备 巴克什　绝密", GATES)  # 全角空格
    assert query.kind == "gate"


def test_custom_command_disabled_by_default() -> None:
    query = parse("凑战备 50000", GATES)
    assert query.kind == "help"
    assert "暂时关闭" in query.error
    assert query.error.startswith(CUSTOM_DISABLED_NOTE[:10])


def test_custom_command_when_enabled() -> None:
    query = parse("凑战备 5万 不要枪", GATES, custom_enabled=True)
    assert query.kind == "custom"
    assert query.target == 50000
    assert query.slots.gun is False


def test_custom_validation_when_enabled() -> None:
    assert parse("凑战备 1000", GATES, custom_enabled=True).kind == "help"
    assert parse("凑战备", GATES, custom_enabled=True).kind == "help"
    assert parse("凑战备 abc", GATES, custom_enabled=True).kind == "help"


@pytest.mark.parametrize("text", ["今天天气不错", "我说卡战备很厉害", "看看凑战备怎么玩", ""])
def test_not_a_command(text: str) -> None:
    assert parse(text, GATES) is None


@pytest.mark.parametrize(
    ("token", "expected"),
    [("50000", 50000), ("5万", 50000), ("5.5w", 55000), ("abc", None), ("", None)],
)
def test_parse_amount(token: str, expected: int | None) -> None:
    assert parse_amount(token) == expected
