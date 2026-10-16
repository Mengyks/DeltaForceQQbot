"""卡战备相关数据模型。

**字段语义已用真实接口响应核对（2026-10-15）**，与直觉不同，务必按下面的结论理解：

- 方案级 `jz`：整套配装的**总战备值**（≥ 门槛，例如 550013）。
- 方案级 `cz`：**花费 − 门槛**。负值表示"比门槛少花这么多钱"，例如花费 48.9884 万、门槛 55 万
  → cz = -60116。同一档位内按 cz 排序等价于按花费排序。
- 装备级 `jz`：**该件价格 − 该件战备值贡献**（不是战备值本身！）。例如某瞄具价格 7.34 万、
  贡献 8.82 万战备值 → jz = -14834。因此物品战备值 = price − jz。
- 装备级 `bl`：假账百分比 = 100 × jz ÷ (price − jz)。**负值表示战备值虚高（划算）**，正值表示溢价。
- 装备级 `jiazhang`：0/1 标记，样本中等于「bl < 0」（即是否为战备值虚高的件）。

校验依据：Σ(装备 price − jz) = 方案 jz；Σ(装备 jz) ≈ 方案 cz（样本误差 <20）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from deltaforce_bot.adapters.errors import DataSourceError


def _to_int(value: Any, default: int = 0) -> int:
    """把可能是字符串/浮点的数值字段转成 int，失败时取默认值。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SlotFlags:
    """自定义凑战备的部位开关（该功能当前关闭，保留代码以便随时恢复）。"""

    gun: bool = True
    helmet: bool = True
    armor: bool = True
    chest: bool = True
    bag: bool = True
    pistol: bool = True
    dual_gun_off: bool = False

    def with_slot(self, slot: str, enabled: bool) -> "SlotFlags":
        if slot == "dual_gun_off":
            return SlotFlags(**{**self.__dict__, "dual_gun_off": enabled})
        if slot not in {"gun", "helmet", "armor", "chest", "bag", "pistol"}:
            raise ValueError(f"未知部位：{slot}")
        return SlotFlags(**{**self.__dict__, slot: enabled})


class LoadoutItem(BaseModel):
    """方案中的单件装备。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    type: str | None = None
    pic: str | None = None
    price: int = 0  # 市价（哈夫币）
    jz: int = 0  # 价格 − 战备值贡献（见模块文档，不是战备值本身）
    grade: int | None = None
    jiazhang: int | None = None  # 0/1：是否为战备值虚高的件
    bl: int | None = None  # 假账百分比，负值 = 战备值虚高（划算）
    exchange: str | None = None  # 非空表示来自军需处兑换

    @property
    def readiness(self) -> int:
        """该件贡献的战备值 = 价格 − jz。"""
        return self.price - self.jz

    @property
    def fake_gain(self) -> int:
        """假账收益：正数表示战备值高于价格（划算），负数表示溢价。"""
        return -self.jz

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "LoadoutItem":
        if not isinstance(raw, Mapping):
            raise DataSourceError("装备明细格式异常")
        return cls(
            name=str(raw.get("name") or ""),
            type=raw.get("type") or None,
            pic=raw.get("pic") or None,
            price=_to_int(raw.get("price")),
            jz=_to_int(raw.get("jz")),
            grade=_to_int_or_none(raw.get("grade")),
            jiazhang=_to_int_or_none(raw.get("jiazhang")),
            bl=_to_int_or_none(raw.get("bl")),
            exchange=raw.get("exchange") or None,
        )


class LoadoutPlan(BaseModel):
    """一套完整配装方案。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    jz: int = 0  # 整套总战备值
    price: int = 0  # 整套花费（哈夫币）
    cz: int | None = None  # 花费 − 门槛（负值 = 比门槛少花）
    index: int | None = None
    group: str | None = None  # 所属套装组（V4 接口）
    is_exchange: bool = False  # 是否含军需处兑换
    items: list[LoadoutItem] = []

    @property
    def saved(self) -> int | None:
        """比门槛少花的钱（cz 为负时为正数）；cz 缺失或为溢价时返回 None。"""
        if self.cz is None or self.cz >= 0:
            return None
        return -self.cz

    @property
    def overpaid(self) -> int | None:
        """比门槛多花的钱（cz 为正时）。"""
        if self.cz is None or self.cz <= 0:
            return None
        return self.cz

    @classmethod
    def from_api(cls, raw: Mapping[str, Any], *, group: str | None = None) -> "LoadoutPlan":
        if not isinstance(raw, Mapping):
            raise DataSourceError("配装方案格式异常")
        items_raw = raw.get("data")
        if items_raw is None:
            items_raw = []
        if not isinstance(items_raw, list):
            raise DataSourceError("配装方案的装备明细不是列表")
        return cls(
            name=str(raw.get("name") or ""),
            jz=_to_int(raw.get("jz")),
            price=_to_int(raw.get("price")),
            cz=_to_int_or_none(raw.get("cz")),
            index=_to_int_or_none(raw.get("index")),
            group=group,
            items=[LoadoutItem.from_api(item) for item in items_raw],
        )


class TierGroup(BaseModel):
    """V4 接口返回的套装组（一个组下含多套方案，组名如「花费最少-单枪」「花费最少-兑换」）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    zb: int | None = None  # 该组对应的战备值门槛（接口给的权威值）
    count: int | None = None
    date: int | None = None
    date_text: str | None = None
    plans: list[LoadoutPlan] = []

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "TierGroup":
        if not isinstance(raw, Mapping):
            raise DataSourceError("套装组格式异常")
        plans_raw = raw.get("list")
        if plans_raw is None:
            plans_raw = []
        if not isinstance(plans_raw, list):
            raise DataSourceError("套装组的方案列表不是列表")
        group_name = str(raw.get("name") or "")
        return cls(
            name=group_name,
            zb=_to_int_or_none(raw.get("zb")),
            count=_to_int_or_none(raw.get("count")),
            date=_to_int_or_none(raw.get("date")),
            date_text=raw.get("dateT") or None,
            plans=[LoadoutPlan.from_api(plan, group=group_name) for plan in plans_raw],
        )
