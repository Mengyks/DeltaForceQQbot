"""制造台利润的数据模型（纯数据，不依赖框架/网络）。

接口字段口径（2026-10-25 实测 + 官方数据版文档交叉核对）：
- ``priceMax``：交易行当前售价（未扣手续费）
- ``sxf``：手续费（交易 + 上架）
- ``price``：单次制造利润（已扣材料成本和手续费）
- ``price_hour``：每小时利润 = price / period
- ``period``：制造时长（小时，可能是小数）

卡片只使用 name / pic / grade / price / price_hour 等字段；
``priceMax`` 与 ``sxf`` 保留给后续需要展示售价/手续费时使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CRAFTING_LEVEL = 3

STATIONS: tuple[tuple[int, str], ...] = (
    (1, "技术中心"),
    (2, "工作台"),
    (3, "制药台"),
    (4, "防具台"),
)


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def format_money(value: int) -> str:
    return f"{value:,}"


def format_signed_money(value: int) -> str:
    return f"+{value:,}" if value > 0 else f"{value:,}"


@dataclass(frozen=True)
class CraftingItem:
    name: str
    grade: int
    period: float
    price: int
    price_hour: int
    pic: str = ""
    object_id: str = ""
    oid: str = ""
    primary_class: str = ""
    second_class: str = ""
    second_class_cn: str = ""
    unlock_level: int = 0
    price_max: int = 0
    sxf: int = 0

    @property
    def is_negative(self) -> bool:
        return self.price < 0

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "CraftingItem":
        if not isinstance(raw, Mapping):
            raise ValueError("制造利润物品格式异常")
        name = _to_text(raw.get("name"))
        if not name:
            raise ValueError("制造利润物品缺少名称")
        return cls(
            name=name,
            grade=_to_int(raw.get("grade")),
            period=_to_float(raw.get("period")),
            price=_to_int(raw.get("price")),
            price_hour=_to_int(raw.get("price_hour")),
            pic=_to_text(raw.get("pic")),
            object_id=_to_text(raw.get("objectID")),
            oid=_to_text(raw.get("oid")),
            primary_class=_to_text(raw.get("primaryClass")),
            second_class=_to_text(raw.get("secondClass")),
            second_class_cn=_to_text(raw.get("secondClassCN")),
            unlock_level=_to_int(raw.get("unlockLevel")),
            price_max=_to_int(raw.get("priceMax")),
            sxf=_to_int(raw.get("sxf")),
        )


@dataclass(frozen=True)
class CraftingStationResult:
    station_id: int
    label: str
    item: CraftingItem | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.item is not None

    @property
    def display_name(self) -> str:
        return self.item.name if self.item is not None else "数据获取失败"


@dataclass(frozen=True)
class CraftingResult:
    stations: tuple[CraftingStationResult, ...]
    fetched_at: float | None = None  # 这批数据实际查询/获取的时间（缓存复用则保持原时间）

    @property
    def complete(self) -> bool:
        return bool(self.stations) and all(station.ok for station in self.stations)

    @property
    def available_count(self) -> int:
        return sum(1 for station in self.stations if station.ok)

    def signature(self) -> tuple:
        """图片缓存键：只在四个台都成功时用于缓存完整卡片。"""
        return tuple(
            (
                station.station_id,
                station.item.name if station.item else None,
                station.item.grade if station.item else None,
                station.item.price if station.item else None,
                station.item.price_hour if station.item else None,
            )
            for station in self.stations
        )

    def cache_key(self) -> str:
        parts: list[str] = []
        for station in self.stations:
            item = station.item
            if item is None:
                parts.append(f"{station.station_id}:-")
            else:
                parts.append(
                    f"{station.station_id}:{item.name}:{item.grade}:{item.price}:{item.price_hour}"
                )
        # 数据刷新后即使物品没变，也要重新渲染以更新“查询时间”
        stamp = int(self.fetched_at // 60) if self.fetched_at is not None else 0
        return f"crafting:{stamp}:" + "|".join(parts)
