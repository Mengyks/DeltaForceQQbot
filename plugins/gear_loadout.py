"""卡战备命令入口（NoneBot 插件）。

只做四件事：识别命令、交给 deltaforce_bot.services.commands 处理、发图片或文字、
并在**图片发送成功后**把它写入缓存（用户要求：发送成功才缓存）。
"""

from __future__ import annotations

import base64
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.commands import handle_gear_command
from deltaforce_bot.services.text import COMMAND_PATTERN

logger = logging.getLogger(__name__)


def _is_gear_command(event: MessageEvent) -> bool:
    return bool(COMMAND_PATTERN.match(event.get_plaintext() or ""))


gear_matcher = on_message(rule=Rule(_is_gear_command), priority=10, block=True)


@gear_matcher.handle()
async def handle_gear(event: MessageEvent) -> None:
    runtime = get_runtime()
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    text = event.get_plaintext() or ""

    reply = await handle_gear_command(runtime, text, group_id=group_id, user_id=event.user_id)
    if reply is None:
        logger.debug("未产生回复：%r", text[:40])
        return

    if reply.image:
        segment = MessageSegment.image(f"base64://{base64.b64encode(reply.image).decode()}")
        try:
            await gear_matcher.send(Message(segment))
        except Exception as exc:  # noqa: BLE001  发图失败不能影响服务，直接回文字
            logger.warning("发送图片失败，改用文字版：%s: %s", exc.__class__.__name__, exc)
        else:
            logger.info("回复卡战备查询（图片 %s KB）：%r", len(reply.image) // 1024, text[:40])
            if reply.image_cache_key and runtime.settings.image_cache_ttl > 0:
                runtime.image_cache.set(reply.image_cache_key, reply.image, runtime.settings.image_cache_ttl)
                logger.debug("图片已缓存：%s", reply.image_cache_key)
            return

    logger.info("回复卡战备查询（文字 %s 字）：%r", len(reply.text), text[:40])
    await gear_matcher.finish(reply.text)
