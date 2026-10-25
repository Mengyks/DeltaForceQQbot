"""制造台利润业务层：调用数据帝 Pro、解析、每台选利润最高的一个。

按用户确认：
- 四个台、固定 Lv3；
- 按需查询，缓存 12 小时；
- 只展示单次利润与每小时利润；
- 每台只取 ``price_hour`` 最高的一条（同价时取单次利润更高者）。
"""

from __future__ import annotations

import logging
import time

from deltaforce_bot.adapters.errors import (
    DataSourceAuthError,
    DataSourceError,
    QuotaExhausted,
)
from deltaforce_bot.adapters.orzice import OrziceClient
from deltaforce_bot.config import Settings
from deltaforce_bot.models.crafting import (
    CRAFTING_LEVEL,
    STATIONS,
    CraftingItem,
    CraftingResult,
    CraftingStationResult,
    format_signed_money,
)

logger = logging.getLogger(__name__)

CRAFTING_PATH = "/v1/sjz_api/manufacturePro"
CRAFTING_COST = 1


class CraftingUnavailable(RuntimeError):
    """所有制造台都取不到数据，或数据源鉴权/额度有问题。"""


class CraftingService:
    def __init__(self, settings: Settings, client: OrziceClient) -> None:
        self._settings = settings
        self._client = client

    async def get_profit(self) -> CraftingResult:
        results: list[CraftingStationResult] = []
        fetched_times: list[float] = []
        for station_id, label in STATIONS:
            try:
                raw = await self._client.get(
                    CRAFTING_PATH,
                    params={"t": station_id, "l": CRAFTING_LEVEL},
                    ttl=max(int(getattr(self._settings, "crafting_cache_ttl", 43200)), 60),
                    cost=CRAFTING_COST,
                )
            except DataSourceAuthError as exc:
                logger.error("制造台利润数据源鉴权失败：%s", exc)
                raise CraftingUnavailable(
                    "数据源未配置或 token 失效，请管理员检查配置后重试。"
                ) from exc
            except QuotaExhausted as exc:
                logger.warning("制造台利润额度熔断：%s", exc)
                raise CraftingUnavailable(f"⚠️ {exc}") from exc
            except DataSourceError as exc:
                logger.warning("制造台利润获取失败（%s）：%s", label, exc)
                results.append(
                    CraftingStationResult(
                        station_id=station_id,
                        label=label,
                        error=str(exc),
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001  单个台失败不影响其他台
                logger.exception(
                    "制造台利润处理异常（%s）：%s: %s",
                    label,
                    exc.__class__.__name__,
                    exc,
                )
                results.append(
                    CraftingStationResult(
                        station_id=station_id,
                        label=label,
                        error="处理失败",
                    )
                )
                continue

            try:
                items = parse_items(raw)
                top = select_top(items)
            except (TypeError, ValueError) as exc:
                logger.warning("制造台利润解析失败（%s）：%s", label, exc)
                results.append(
                    CraftingStationResult(
                        station_id=station_id,
                        label=label,
                        error="返回格式异常",
                    )
                )
                continue

            if top is None:
                results.append(
                    CraftingStationResult(
                        station_id=station_id,
                        label=label,
                        error="没有可用物品",
                    )
                )
            else:
                results.append(
                    CraftingStationResult(
                        station_id=station_id,
                        label=label,
                        item=top,
                    )
                )
                try:
                    age = self._client.cache_age(
                        CRAFTING_PATH,
                        {"t": station_id, "l": CRAFTING_LEVEL},
                    )
                except Exception:  # noqa: BLE001  测试替身可能没有 cache_age
                    age = None
                if age is not None:
                    fetched_times.append(time.time() - float(age))

        result = CraftingResult(
            tuple(results),
            fetched_at=min(fetched_times) if fetched_times else time.time(),
        )
        logger.debug(
            "制造台利润获取完成：%s/%s 个台",
            result.available_count,
            len(result.stations),
        )
        return result


def parse_items(raw: object) -> list[CraftingItem]:
    """把接口的 data 列表解析成 CraftingItem；宽容处理单条对象。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        items: list[CraftingItem] = []
        for entry in raw:
            try:
                items.append(CraftingItem.from_api(entry))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return items
    if isinstance(raw, dict) and "name" in raw:
        return [CraftingItem.from_api(raw)]
    raise ValueError("制造利润数据格式无法识别")


def select_top(items: list[CraftingItem]) -> CraftingItem | None:
    """按时利润降序，同价时按单次利润降序。"""
    if not items:
        return None
    return max(items, key=lambda item: (item.price_hour, item.price))


def format_crafting_text(result: CraftingResult, *, cache_hours: int = 12) -> str:
    lines = [f"【每日利润 · Lv{CRAFTING_LEVEL}】"]
    for station in result.stations:
        if station.item is None:
            lines.append(f"{station.label}：暂时获取失败")
            continue
        item = station.item
        lines.append(f"{station.label}：{item.name}")
        lines.append(
            f"  单次利润 {format_signed_money(item.price)}"
            f" ｜ 每小时 {format_signed_money(item.price_hour)}"
        )
    lines.append("")
    lines.append(f"数据来源：数据帝 Pro ｜ {cache_hours} 小时缓存")
    return "\n".join(lines)
