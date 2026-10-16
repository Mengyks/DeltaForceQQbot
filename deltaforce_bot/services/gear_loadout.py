"""卡战备业务逻辑：按「地图 + 难度」取方案。

数据来源见 docs/02-数据源接口清单.md。字段语义（cz / jz / bl）已用真实响应核对，
结论写在 models/loadout.py 的模块文档里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from deltaforce_bot.adapters.errors import DataSourceError
from deltaforce_bot.adapters.orzice import OrziceClient
from deltaforce_bot.config import Settings
from deltaforce_bot.models.gate import Gate
from deltaforce_bot.models.loadout import LoadoutPlan, SlotFlags, TierGroup

V4_PATH = "/v1/sjz_api/jzv4_zb"
DIY_PATH = "/v1/sjz_api/jzv3_diy"

V4_COST = 1  # 平台资源次数/次
DIY_COST = 10

MIN_TARGET = 1500
MAX_TARGET = 780000

EXCHANGE_KEYWORD = "兑换"


@dataclass
class GateResult:
    """一个入口（地图 + 难度）下的查询结果。"""

    gate: Gate
    best: LoadoutPlan | None  # 最省的一套（详细展示）
    alternatives: list[LoadoutPlan] = field(default_factory=list)  # 其他配装思路各取最省一套
    total: int = 0  # 候选方案总数
    threshold: int = 0  # 门槛：优先用接口返回的 zb（权威值）
    updated_at: str | None = None  # 数据源自己的更新时间
    cache_age: float | None = None  # 本机缓存了多久（秒）
    include_exchange: bool = False


class GearLoadoutService:
    def __init__(self, client: OrziceClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def by_gate(self, gate: Gate, *, include_exchange: bool, top: int) -> GateResult:
        """查询某个入口的最省方案。

        默认排除军需处兑换组（与平台「花费最少」口径一致），因为兑换需要玩家自备兑换券；
        用户明确加「兑换」时才纳入。
        """
        params = {"lv": gate.lv}
        raw = await self._client.get(
            V4_PATH,
            params=params,
            ttl=self._settings.cache_ttl_tier,
            cost=V4_COST,
        )
        groups = parse_groups(raw)

        plans: list[LoadoutPlan] = []
        for group in groups:
            group_is_exchange = EXCHANGE_KEYWORD in (group.name or "")
            for plan in group.plans:
                if group_is_exchange or EXCHANGE_KEYWORD in (plan.name or ""):
                    plan.is_exchange = True
                plans.append(plan)

        total = len(plans)
        if not include_exchange:
            plans = [plan for plan in plans if not plan.is_exchange]

        # 同档位内门槛相同，按花费升序即等价于平台的 cz 升序（「花费最少」）
        plans.sort(key=cheap_key)
        plans = dedupe_plans(plans)

        best: LoadoutPlan | None = None
        alternatives: list[LoadoutPlan] = []
        if plans:
            best = plans[0]
            # 其他配装思路（组）各取最省一套，便于用户换思路
            for plan in plans[1:]:
                if plan.group == best.group:
                    continue
                if any(item.group == plan.group for item in alternatives):
                    continue
                alternatives.append(plan)
                if len(alternatives) >= top:
                    break

        threshold = max((group.zb for group in groups if group.zb), default=gate.threshold)
        updated_at = max((group.date_text for group in groups if group.date_text), default=None)

        return GateResult(
            gate=gate,
            best=best,
            alternatives=alternatives,
            total=total,
            threshold=threshold,
            updated_at=updated_at,
            cache_age=self._client.cache_age(V4_PATH, params),
            include_exchange=include_exchange,
        )

    async def custom(self, target: int, *, slots: SlotFlags, exchange: bool) -> LoadoutPlan:
        """自定义凑战备（成本 10 次资源，功能默认关闭）。"""
        validate_target(target)
        params: dict[str, Any] = {
            "zb": target,
            "exchange": 1 if exchange else 0,
            "is_gun": 1 if slots.gun else 0,
            "is_tk": 1 if slots.helmet else 0,
            "is_hj": 1 if slots.armor else 0,
            "is_xg": 1 if slots.chest else 0,
            "is_bb": 1 if slots.bag else 0,
            "is_sq": 1 if slots.pistol else 0,
        }
        if slots.dual_gun_off:
            params["is_gun2_off"] = 1

        raw = await self._client.get(
            DIY_PATH,
            params=params,
            ttl=self._settings.cache_ttl_diy,
            cost=DIY_COST,
        )
        return parse_single_plan(raw)


def validate_target(target: int) -> None:
    if not isinstance(target, int) or not (MIN_TARGET <= target <= MAX_TARGET):
        raise ValueError(f"目标战备值需要在 {MIN_TARGET}~{MAX_TARGET} 之间（当前：{target}）")


def cheap_key(plan: LoadoutPlan) -> tuple[int, int]:
    """花费升序；同价时按 cz 升序（cz = 花费 − 门槛，同档位下与花费等价）。"""
    return (plan.price, plan.cz if plan.cz is not None else plan.price)


def plan_signature(plan: LoadoutPlan) -> tuple:
    """方案的装备构成签名，用于去掉完全重复的方案。"""
    return tuple(sorted((item.name, item.price) for item in plan.items))


def dedupe_plans(plans: list[LoadoutPlan]) -> list[LoadoutPlan]:
    """去掉装备构成完全相同的方案（保留排在前面的、更省的那套）。

    注意不能按方案名去重：真实数据里同一组下的 5 套方案共用组名（如「花费最少-单枪」）。
    """
    seen: set[tuple] = set()
    result: list[LoadoutPlan] = []
    for plan in plans:
        signature = plan_signature(plan)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(plan)
    return result


def parse_groups(raw: Any) -> list[TierGroup]:
    """把接口返回的数据宽容地解析成套装组列表。"""
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        if isinstance(raw.get("list"), list):
            return [TierGroup.from_api(raw)]
        if isinstance(raw.get("data"), list) and ("jz" in raw or "price" in raw):
            plan = LoadoutPlan.from_api(raw)
            return [TierGroup(name=plan.name or "", plans=[plan])]
        return []
    if isinstance(raw, list):
        groups: list[TierGroup] = []
        for item in raw:
            groups.extend(parse_groups(item))
        return groups
    raise DataSourceError("卡战备数据格式无法识别")


def parse_single_plan(raw: Any) -> LoadoutPlan:
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], Mapping) else None
    if not isinstance(raw, Mapping):
        raise DataSourceError("自定义凑战备未返回有效方案")
    return LoadoutPlan.from_api(raw)
