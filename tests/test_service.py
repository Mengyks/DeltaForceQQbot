"""卡战备服务测试：取数、筛选、排序、备选思路与参数校验。"""

from __future__ import annotations

import pytest

from deltaforce_bot.adapters.errors import DataSourceError
from deltaforce_bot.models.loadout import LoadoutPlan, SlotFlags
from deltaforce_bot.services.gear_loadout import (
    DIY_PATH,
    V4_PATH,
    GearLoadoutService,
    cheap_key,
    dedupe_plans,
    parse_groups,
    plan_signature,
)
from tests.support import FakeClient, load_data, load_gates, make_settings

GATES = load_gates()


def gate(lv: int):
    return next(item for item in GATES.gates if item.lv == lv)


def service_with(payload, **overrides):
    client = FakeClient(payload)
    return GearLoadoutService(client, make_settings(**overrides)), client


async def test_by_gate_returns_cheapest_and_alternatives() -> None:
    service, client = service_with(load_data("jzv4_zb_lv2.json", real=True))
    result = await service.by_gate(gate(2), include_exchange=False, top=3)

    assert result.threshold == 550000  # 来自接口 zb，而非配置
    assert result.updated_at is not None
    assert result.best is not None
    assert result.best.is_exchange is False

    for plan in result.alternatives:
        assert plan.price >= result.best.price
        assert plan.is_exchange is False
    groups = [plan.group for plan in result.alternatives]
    assert len(groups) == len(set(groups))  # 每个配装思路只出一条
    assert len(result.alternatives) == 3

    assert client.calls == [
        {"path": V4_PATH, "params": {"lv": 2}, "ttl": 14400, "cost": 1},
    ]


async def test_by_gate_with_exchange_prefers_exchange_plan() -> None:
    service, _ = service_with(load_data("jzv4_zb_lv2.json", real=True))
    result = await service.by_gate(gate(2), include_exchange=True, top=3)

    assert result.best is not None
    assert result.best.is_exchange is True
    assert result.best.price < 460000  # 实测兑换组最省约 44.9 万


async def test_by_gate_counts_all_candidate_plans() -> None:
    service, _ = service_with(load_data("jzv4_zb_lv2.json", real=True))
    result = await service.by_gate(gate(2), include_exchange=False, top=3)

    assert result.total == 35  # 7 组 × 5 套


async def test_by_gate_without_data_falls_back_to_config_threshold() -> None:
    service, _ = service_with([])
    result = await service.by_gate(gate(2), include_exchange=False, top=3)

    assert result.best is None
    assert result.total == 0
    assert result.threshold == gate(2).threshold


async def test_by_gate_uses_requested_gate_level() -> None:
    service, client = service_with(load_data("jzv4_zb_lv0.json", real=True))
    await service.by_gate(gate(0), include_exchange=False, top=3)

    assert client.calls[0]["params"] == {"lv": 0}


def test_three_low_gates_share_cache_key() -> None:
    """零号大坝 / 长弓溪谷 / AZ3 的机密难度同门槛（lv=0），因此共用同一份 4 小时缓存。

    这就是"查这三张图只请求一次"的实现方式：缓存键只包含 lv，不包含地图名。
    """
    from deltaforce_bot.adapters.orzice import OrziceClient

    gates = [g for g in GATES.gates if g.map in {"零号大坝", "长弓溪谷", "az3"}]
    assert len(gates) == 3
    assert {g.lv for g in gates} == {0}

    keys = {OrziceClient.cache_key(V4_PATH, {"lv": g.lv}) for g in gates}
    assert len(keys) == 1  # 三个入口 → 同一个缓存键


def test_cheap_key_sorts_by_price() -> None:
    plans = [
        LoadoutPlan(name="b", price=200, cz=-10),
        LoadoutPlan(name="a", price=100, cz=-20),
    ]
    plans.sort(key=cheap_key)
    assert [plan.name for plan in plans] == ["a", "b"]


def test_dedupe_plans_removes_identical_compositions() -> None:
    from deltaforce_bot.models.loadout import LoadoutItem

    item = LoadoutItem(name="头盔", price=100, jz=-10)
    first = LoadoutPlan(name="组A", price=100, items=[item])
    duplicate = LoadoutPlan(name="组A", price=100, items=[item])
    other = LoadoutPlan(name="组A", price=120, items=[LoadoutItem(name="头盔", price=120, jz=-5)])

    assert plan_signature(first) == plan_signature(duplicate)
    assert [plan.price for plan in dedupe_plans([first, duplicate, other])] == [100, 120]


def test_parse_groups_accepts_single_plan_and_rejects_garbage() -> None:
    single = parse_groups({"name": "自定义", "jz": 100, "price": 10, "data": [{"name": "枪", "jz": 100, "price": 10}]})
    assert len(single) == 1
    assert single[0].plans[0].name == "自定义"

    assert parse_groups(None) == []

    with pytest.raises(DataSourceError):
        parse_groups("unexpected")


async def test_custom_maps_all_parameters() -> None:
    service, client = service_with(load_data("jzv3_diy.json"), cache_ttl_diy=99)
    plan = await service.custom(50000, slots=SlotFlags(), exchange=False)

    assert client.calls == [
        {
            "path": DIY_PATH,
            "params": {
                "zb": 50000,
                "exchange": 0,
                "is_gun": 1,
                "is_tk": 1,
                "is_hj": 1,
                "is_xg": 1,
                "is_bb": 1,
                "is_sq": 1,
            },
            "ttl": 99,
            "cost": 10,
        }
    ]
    assert plan.name == "自定义"


async def test_custom_disabled_slots_and_validation() -> None:
    service, client = service_with(load_data("jzv3_diy.json"))
    await service.custom(30000, slots=SlotFlags(gun=False, dual_gun_off=True), exchange=True)

    params = client.calls[0]["params"]
    assert params["is_gun"] == 0 and params["is_gun2_off"] == 1 and params["exchange"] == 1

    with pytest.raises(ValueError):
        await service.custom(1000, slots=SlotFlags(), exchange=False)
    assert len(client.calls) == 1  # 越界不发请求
