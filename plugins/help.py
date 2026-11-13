"""``/帮助`` 插件：返回五点老师的帮助页图片卡片。

卡片是静态内容（功能指令 + 示例），渲染成功后缓存一天；
渲染/发图失败回退同内容的文字版。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.adapters.cache import TTLCache
from deltaforce_bot.render.help_card import (
    HELP_BACKGROUND_NAME,
    build_help_card_context,
    render_help_card,
)
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.help import format_help_text
from deltaforce_bot.services.text import HELP_COMMAND_PATTERN

logger = logging.getLogger(__name__)

_HELP_CACHE_KEY = "help-v3"
_HELP_IMAGE_TTL = 86400
_image_cache: TTLCache = TTLCache()


def _is_help_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return bool(HELP_COMMAND_PATTERN.match((event.get_plaintext() or "").strip()))


help_matcher = on_message(rule=Rule(_is_help_command), priority=10, block=True)


@help_matcher.handle()
async def handle_help(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    image_bytes: bytes | None = None
    hit, cached = _image_cache.get(_HELP_CACHE_KEY)
    if hit:
        image_bytes = cached

    if image_bytes is None:
        background = Path(runtime.settings.assets_dir) / "backgrounds" / HELP_BACKGROUND_NAME
        context = build_help_card_context(background)
        image_bytes = await render_help_card(
            getattr(runtime, "renderer", None),
            context,
        )

    if image_bytes is not None:
        try:
            await help_matcher.send(
                Message(
                    MessageSegment.image(
                        "base64://" + base64.b64encode(image_bytes).decode()
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001  发图失败回退文字
            logger.warning("发送帮助图片失败，改用文字：%s: %s", exc.__class__.__name__, exc)
        else:
            _image_cache.set(_HELP_CACHE_KEY, image_bytes, _HELP_IMAGE_TTL)
            logger.info(
                "回复帮助（图片 %s KB）：group=%s",
                len(image_bytes) // 1024,
                event.group_id,
            )
            return

    logger.info("回复帮助（文字）：group=%s", event.group_id)
    await help_matcher.finish(format_help_text())
