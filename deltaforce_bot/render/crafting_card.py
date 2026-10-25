"""制造台利润卡片的上下文装配与渲染。

原型（assets/items/html_prototype/制作台利润原型.png）：
- 标题「每日利润」；
- 2×2 四个制造台；
- 每格：制造台名称 + 物品图（等级边框色）+ 物品名 + 单次利润/每小时利润。

只展示单次利润和每小时利润；不展示售价、手续费、材料成本。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from deltaforce_bot.models.crafting import (
    CRAFTING_LEVEL,
    CraftingResult,
    format_signed_money,
)
from deltaforce_bot.render.assets import file_url
from deltaforce_bot.render.card import LEVEL_COLORS
from deltaforce_bot.render.crafting_assets import CraftingAssetLibrary
from deltaforce_bot.render.renderer import CardRenderer, RenderError

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "crafting_profit.html.j2"
CARD_VIEWPORT_WIDTH = 920
DEFAULT_TARGET_BYTES = 350 * 1024
DEFAULT_HARD_LIMIT_BYTES = 600 * 1024
QUALITY_LADDER = (86, 78, 70, 62)
DEFAULT_BORDER_COLOR = "#7a8794"
PROFIT_POSITIVE = "#12805c"
PROFIT_NEGATIVE = "#d33a3a"


@dataclass(frozen=True)
class CraftingCardItem:
    station: str
    item_name: str
    image_url: str | None
    border_color: str
    grade: int
    profit_text: str
    hourly_text: str
    positive: bool
    error: bool = False


@dataclass(frozen=True)
class CraftingCardContext:
    title: str
    subtitle: str
    query_time: str
    source_note: str
    items: list[CraftingCardItem]


def format_query_time(timestamp: float | None) -> str:
    value = time.time() if timestamp is None else float(timestamp)
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def build_crafting_card_context(
    result: CraftingResult,
    assets: CraftingAssetLibrary | None,
    *,
    level: int = CRAFTING_LEVEL,
    cache_hours: int = 12,
) -> CraftingCardContext:
    items: list[CraftingCardItem] = []
    for station in result.stations:
        item = station.item
        if item is None:
            items.append(
                CraftingCardItem(
                    station=station.label,
                    item_name="数据获取失败",
                    image_url=None,
                    border_color=DEFAULT_BORDER_COLOR,
                    grade=0,
                    profit_text="—",
                    hourly_text="—",
                    positive=False,
                    error=True,
                )
            )
            continue

        image_path: Path | None = None
        if assets is not None:
            image_path = assets.find(station.station_id, item)
        items.append(
            CraftingCardItem(
                station=station.label,
                item_name=item.name,
                image_url=file_url(image_path) if image_path is not None else None,
                border_color=LEVEL_COLORS.get(item.grade, DEFAULT_BORDER_COLOR),
                grade=item.grade,
                profit_text=format_signed_money(item.price),
                hourly_text=format_signed_money(item.price_hour),
                positive=item.price >= 0 and item.price_hour >= 0,
            )
        )

    return CraftingCardContext(
        title="每日利润",
        subtitle=f"特勤处制造 · Lv{level}",
        query_time=format_query_time(result.fetched_at),
        source_note=f"数据来源：数据帝 Pro ｜ 缓存 {cache_hours} 小时 ｜ 单位：哈夫币",
        items=items,
    )


async def render_crafting_card(
    renderer: CardRenderer | None,
    context: CraftingCardContext,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    hard_limit_bytes: int = DEFAULT_HARD_LIMIT_BYTES,
) -> bytes | None:
    """渲染制造台利润卡片；按质量阶梯压缩，失败返回 None。"""
    if renderer is None:
        return None

    best: bytes | None = None
    best_quality = 0
    for quality in QUALITY_LADDER:
        try:
            image = await renderer.render_template(
                context,
                template_name=TEMPLATE_NAME,
                screenshot_type="jpeg",
                quality=quality,
                viewport_width=CARD_VIEWPORT_WIDTH,
            )
        except RenderError as exc:
            logger.warning("制造台利润卡片渲染失败（质量 %s）：%s", quality, exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "制造台利润卡片渲染异常（质量 %s）：%s: %s",
                quality,
                exc.__class__.__name__,
                exc,
            )
            break

        if best is None or len(image) < len(best):
            best = image
            best_quality = quality
        if len(image) <= target_bytes:
            logger.info(
                "制造台利润卡片渲染完成：%s KB（JPEG 质量 %s）",
                len(image) // 1024,
                quality,
            )
            return image

    if best is None:
        logger.warning("制造台利润卡片渲染失败，改用文字")
        return None
    if len(best) <= hard_limit_bytes:
        logger.info(
            "制造台利润卡片压缩到 %s KB（JPEG 质量 %s，未达目标但低于硬上限）",
            len(best) // 1024,
            best_quality,
        )
        return best

    logger.warning(
        "制造台利润卡片压缩后仍然过大（>%s KB），改用文字",
        hard_limit_bytes // 1024,
    )
    return None
