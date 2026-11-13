"""猪猪表情包插件：群友发「猪 / zhu / pig / 🐖 / 🐷」时回一张随机图片。

独立于聊天和命令：只处理群消息文本的**精确**触发词，命中后直接拦截，
不会再交给 LLM 聊天。发送只在收到消息的那个群回复，冷却也按群隔离。
"""

from __future__ import annotations

import base64
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.pig_meme import is_pig_trigger

logger = logging.getLogger(__name__)


def _is_pig_trigger(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not is_pig_trigger(event.get_plaintext() or ""):
        return False
    try:
        settings = get_runtime().settings
    except Exception:  # noqa: BLE001  运行时未初始化时不触发
        return False
    return event.group_id in settings.fun_group_set


pig_meme_matcher = on_message(rule=Rule(_is_pig_trigger), priority=10, block=True)


@pig_meme_matcher.handle()
async def handle_pig_meme(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    service = getattr(runtime, "pig_meme", None)
    if service is None or not service.available:
        logger.debug("猪猪表情包未启用或无图片：group=%s", event.group_id)
        return

    if not service.allow(event.group_id):
        logger.debug("猪猪表情包冷却中：group=%s", event.group_id)
        return

    meme = service.pick()
    if meme is None:
        logger.warning("猪猪表情包读取失败：group=%s", event.group_id)
        return

    segment = MessageSegment.image(
        "base64://" + base64.b64encode(meme.data).decode()
    )
    try:
        await pig_meme_matcher.send(Message(segment))
    except Exception as exc:  # noqa: BLE001  发图失败只记日志，不要刷屏
        logger.warning(
            "发送猪猪表情包失败：group=%s %s: %s",
            event.group_id,
            exc.__class__.__name__,
            exc,
        )
        return

    logger.info(
        "回复猪猪表情包：group=%s file=%s（%s KB）",
        event.group_id,
        meme.path.name,
        len(meme.data) // 1024,
    )
