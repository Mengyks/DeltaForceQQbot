"""真实接口响应的契约测试（数据由 scripts/record_fixture.py 录制，2026-10-15）。

这些断言锁定的是**字段语义**，一旦平台改口径就会失败——这正是我们想要的效果：
与其在生产环境静默展示错误数字，不如在测试里立刻暴露。
"""

from __future__ import annotations

import pytest

from deltaforce_bot.services.gear_loadout import parse_groups
from tests.support import REAL_THRESHOLDS, load_data

GATE_LEVELS = [0, 1, 2, 3, 5]


def load_groups(lv: int):
    return parse_groups(load_data(f"jzv4_zb_lv{lv}.json", real=True))


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_threshold_matches_recorded_value(lv: int) -> None:
    groups = load_groups(lv)
    assert groups, f"lv={lv} 没有解析出任何套装组"
    assert {group.zb for group in groups} == {REAL_THRESHOLDS[lv]}


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_all_groups_have_plans_and_items(lv: int) -> None:
    for group in load_groups(lv):
        assert group.plans, f"组 {group.name} 没有方案"
        for plan in group.plans:
            assert plan.items, f"方案没有装备明细（组 {group.name}）"
            assert plan.price > 0
            assert plan.jz >= REAL_THRESHOLDS[lv]  # 达标才算合格方案


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_plan_cz_equals_price_minus_threshold(lv: int) -> None:
    """cz = 花费 − 门槛（本项目的展示口径「比门槛少花」就建立在这个等式上）。"""
    threshold = REAL_THRESHOLDS[lv]
    for group in load_groups(lv):
        for plan in group.plans:
            assert plan.cz is not None
            assert plan.cz == plan.price - threshold, f"组 {group.name} 的 cz 不符合等式"


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_item_jz_is_price_minus_readiness(lv: int) -> None:
    """装备级 jz = 价格 − 战备值贡献；因此 Σ(price − jz) 应等于整套总战备值。"""
    for group in load_groups(lv):
        for plan in group.plans:
            total = sum(item.price - item.jz for item in plan.items)
            # 允许少量取整误差（样本实测偏差 < 20）
            assert abs(total - plan.jz) <= 50, f"组 {group.name}：Σ(price-jz)={total} vs jz={plan.jz}"


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_jiazhang_flag_matches_bl_sign(lv: int) -> None:
    """jiazhang=1 对应「战备值虚高」（bl < 0），jiazhang=0 对应溢价（bl > 0）。

    bl == 0 是取整边界，实测存在 jiazhang=1 且 bl=0 的样本（如 HK3便携胸挂），
    因此这个取值不做断言。
    """
    for group in load_groups(lv):
        for plan in group.plans:
            for item in plan.items:
                if item.bl is None or item.jiazhang is None:
                    continue
                if item.bl > 0:
                    assert item.jiazhang == 0, f"{item.name}: jiazhang={item.jiazhang} bl={item.bl}"
                elif item.bl < 0:
                    assert item.jiazhang == 1, f"{item.name}: jiazhang={item.jiazhang} bl={item.bl}"


@pytest.mark.parametrize("lv", GATE_LEVELS)
def test_exchange_groups_are_flagged(lv: int) -> None:
    groups = load_groups(lv)
    names = [group.name for group in groups]
    assert any("兑换" in name for name in names), f"lv={lv} 没有兑换组，筛选逻辑需复核"
    exchange_groups = [group for group in groups if "兑换" in group.name]
    for group in exchange_groups:
        assert all(
            item.exchange or item.name for item in group.plans[0].items
        )  # 明细可解析
