"""卡战备命令入口（NoneBot 插件）。

只做三件事：识别命令、交给 deltaforce_bot.services.commands 处理、把文本发回会话。
"""

from __future__ import annotations

import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
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
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    text = event.get_plaintext() or ""
    reply = await handle_gear_command(
        get_runtime(),
        text,
        group_id=group_id,
        user_id=event.user_id,
    )
    if reply:
        logger.info("回复卡战备查询：%r -> %s 字", text[:40], len(reply))
        await gear_matcher.finish(reply)
    else:
        logger.debug("未产生回复：%r", text[:40])
