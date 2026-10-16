"""格式化测试：金额缩写、方案排版、缓存提示、帮助文本。"""

from __future__ import annotations

from deltaforce_bot.models.loadout import LoadoutPlan
from deltaforce_bot.services.gear_loadout import GateResult, GearLoadoutService
from deltaforce_bot.services.text import (
    format_custom_plan,
    format_gate_result,
    format_help,
    humanize_amount,
    humanize_duration,
)
from tests.support import FakeClient, load_data, load_gates, make_settings

GATES = load_gates()


def gate(lv: int):
    return next(item for item in GATES.gates if item.lv == lv)


async def build_result(*, include_exchange: bool = False, top: int = 3) -> GateResult:
    service = GearLoadoutService(FakeClient(load_data("jzv4_zb_lv2.json", real=True)), make_settings())
    return await service.by_gate(gate(2), include_exchange=include_exchange, top=top)


def test_humanize_amount() -> None:
    assert humanize_amount(0) == "0"
    assert humanize_amount(500) == "500"
    assert humanize_amount(9999) == "9999"
    assert humanize_amount(10000) == "1万"
    assert humanize_amount(112500) == "11.25万"
    assert humanize_amount(780000) == "78万"
    assert humanize_amount(-60116) == "-6.01万"


def test_humanize_duration() -> None:
    assert humanize_duration(120) == "2 分钟"
    assert humanize_duration(3 * 3600) == "3.0 小时"


async def test_format_gate_result_contains_plan_and_items() -> None:
    result = await build_result()
    text = format_gate_result(result)

    assert "卡战备 · 巴克什 绝密" in text
    assert "门槛 55万" in text
    assert "最省" in text
    assert "比门槛少花" in text
    assert "总战备" in text and "花费" in text
    assert "其他配装思路" in text
    assert "本档位共 35 套候选" in text
    assert "想包含军需处兑换加「兑换」" in text
    # 装备行包含战备值与花费
    assert "战备" in text and "花" in text


async def test_format_gate_result_with_exchange() -> None:
    result = await build_result(include_exchange=True)
    text = format_gate_result(result)

    assert "含兑换" in text
    assert "想包含军需处兑换加「兑换」" not in text


async def test_format_gate_result_shows_cache_age() -> None:
    result = await build_result()
    result.cache_age = 2 * 3600 + 1800
    text = format_gate_result(result)
    assert "2.5 小时前的缓存" in text

    result.cache_age = 30
    assert "前的缓存" not in format_gate_result(result)


async def test_format_gate_result_empty() -> None:
    service = GearLoadoutService(FakeClient([]), make_settings())
    result = await service.by_gate(gate(2), include_exchange=False, top=3)
    text = format_gate_result(result)
    assert "暂时没取到方案" in text


def test_format_custom_plan() -> None:
    plan = LoadoutPlan.from_api(load_data("jzv3_diy.json"))
    text = format_custom_plan(plan, 50000)
    assert "凑战备 5万" in text
    assert "CAR-15" in text


def test_format_help_lists_gates_and_options() -> None:
    text = format_help(GATES)

    for label in ("零号大坝 机密", "长弓溪谷 机密", "巴克什 机密", "航天基地 机密", "巴克什 绝密", "航天基地 绝密", "潮汐监狱 绝密"):
        assert label in text
    assert "卡战备 <地图> <难度> [兑换]" in text
    assert "常规模式不需要战备值" in text
    assert "凑战备" not in text  # 功能关闭时不在帮助里宣传

    with_custom = format_help(GATES, custom_enabled=True)
    assert "凑战备" in with_custom
