"""制造台利润插件：``/制造利润`` → 四个台 Lv3 的最高利润 → 图片卡片。

按需查询，不做定时任务；数据缓存 12 小时，缓存期内重复查询不再请求数据帝。
"""

from __future__ import annotations

import base64
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.render.crafting_card import (
    build_crafting_card_context,
    render_crafting_card,
)
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.crafting import CraftingUnavailable, format_crafting_text
from deltaforce_bot.services.text import CRAFTING_COMMAND_PATTERN, format_crafting_help

logger = logging.getLogger(__name__)


def _is_crafting_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return bool(CRAFTING_COMMAND_PATTERN.match((event.get_plaintext() or "").strip()))


crafting_matcher = on_message(rule=Rule(_is_crafting_command), priority=10, block=True)


@crafting_matcher.handle()
async def handle_crafting(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    service = getattr(runtime, "crafting", None)
    if service is None:
        await crafting_matcher.finish("制造台利润功能未启用。")

    text = (event.get_plaintext() or "").strip()
    match = CRAFTING_COMMAND_PATTERN.match(text)
    if match is not None and (match.group("args") or "").strip():
        await crafting_matcher.finish(format_crafting_help())

    try:
        result = await service.get_profit()
    except CraftingUnavailable as exc:
        logger.warning("制造台利润不可用：%s", exc)
        await crafting_matcher.finish(str(exc))
    except Exception as exc:  # noqa: BLE001  外部数据/解析异常不能静默
        logger.exception("制造台利润处理失败：%s: %s", exc.__class__.__name__, exc)
        await crafting_matcher.finish("获取制造台利润失败，请稍后再试。")

    if result.available_count == 0:
        await crafting_matcher.finish("暂时获取不到制造台利润，请稍后再试。")

    cache_hours = max(int(getattr(runtime.settings, "crafting_cache_ttl", 43200)), 60) // 3600
    text_reply = format_crafting_text(result, cache_hours=cache_hours)
    image_bytes: bytes | None = None
    cache_key = result.cache_key() if result.complete else None

    if cache_key is not None:
        hit, cached = runtime.crafting_image_cache.get(cache_key)
        if hit:
            image_bytes = cached
    if image_bytes is None:
        context = build_crafting_card_context(
            result,
            getattr(runtime, "crafting_assets", None),
            cache_hours=cache_hours,
        )
        target_kb = max(
            int(getattr(runtime.settings, "crafting_image_max_kb", 350) or 350),
            50,
        )
        image_bytes = await render_crafting_card(
            getattr(runtime, "renderer", None),
            context,
            target_bytes=target_kb * 1024,
        )

    if image_bytes is not None:
        try:
            await crafting_matcher.send(
                Message(
                    MessageSegment.image(
                        "base64://" + base64.b64encode(image_bytes).decode()
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001  发图失败回退文字
            logger.warning(
                "发送制造台利润图片失败，改用文字：%s: %s",
                exc.__class__.__name__,
                exc,
            )
        else:
            logger.info(
                "回复制造台利润（图片 %s KB）：group=%s 台数=%s/%s",
                len(image_bytes) // 1024,
                event.group_id,
                result.available_count,
                len(result.stations),
            )
            if cache_key is not None:
                ttl = max(int(getattr(runtime.settings, "crafting_cache_ttl", 43200)), 60)
                runtime.crafting_image_cache.set(cache_key, image_bytes, ttl)
            return

    logger.info(
        "回复制造台利润（文字）：group=%s 台数=%s/%s",
        event.group_id,
        result.available_count,
        len(result.stations),
    )
    await crafting_matcher.finish(text_reply)
