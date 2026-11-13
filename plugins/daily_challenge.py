"""每日天尊挑战插件：``/每日天尊挑战``。

随机怪物 + 随机武器 + 发送者头像 → HTML + Playwright 截图，
以引用回复的形式只发图片；按“日期 + 群号 + 群友”缓存一天。
"""

from __future__ import annotations

import base64
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.daily_challenge import (
    DailyChallengeAvatarError,
    DailyChallengeError,
    DailyChallengeRenderError,
    DailyChallengeUnavailable,
)
from deltaforce_bot.services.text import DAILY_CHALLENGE_COMMAND_PATTERN

logger = logging.getLogger(__name__)


def _is_daily_challenge_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not DAILY_CHALLENGE_COMMAND_PATTERN.match(event.get_plaintext() or ""):
        return False
    try:
        settings = get_runtime().settings
    except Exception:  # noqa: BLE001  运行时未初始化时不触发
        return False
    return event.group_id in settings.fun_group_set


daily_challenge_matcher = on_message(
    rule=Rule(_is_daily_challenge_command),
    priority=10,
    block=True,
)


def _hunter_name(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    return str(
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or event.user_id
    )


def _image_message(image: bytes, *, message_id: int = 0) -> Message:
    segments: list[MessageSegment] = []
    if message_id:
        segments.append(MessageSegment.reply(message_id))
    segments.append(
        MessageSegment.image("base64://" + base64.b64encode(image).decode())
    )
    return Message(segments)


@daily_challenge_matcher.handle()
async def handle_daily_challenge(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    service = getattr(runtime, "daily_challenge", None)
    if service is None or not service.available:
        await daily_challenge_matcher.finish("每日天尊挑战还没准备好，先找管理员看看～")
        return

    try:
        result = await service.handle(
            group_id=event.group_id,
            user_id=event.user_id,
            hunter_name=_hunter_name(event),
        )
    except DailyChallengeUnavailable:
        await daily_challenge_matcher.finish("每日天尊挑战还没准备好，先找管理员看看～")
        return
    except (DailyChallengeAvatarError, DailyChallengeRenderError) as exc:
        logger.warning("每日天尊挑战生成失败：group=%s %s", event.group_id, exc)
        await daily_challenge_matcher.finish("这次挑战没生成成功，稍后再试～")
        return
    except DailyChallengeError as exc:
        logger.warning("每日天尊挑战失败：group=%s %s", event.group_id, exc)
        await daily_challenge_matcher.finish("这次挑战没生成成功，稍后再试～")
        return
    except Exception:  # noqa: BLE001
        logger.exception("每日天尊挑战未预期错误：group=%s", event.group_id)
        await daily_challenge_matcher.finish("这次挑战没生成成功，稍后再试～")
        return

    message = _image_message(result.image, message_id=event.message_id)
    try:
        await daily_challenge_matcher.send(message)
    except Exception as exc:  # noqa: BLE001  引用回复失败时退回纯图片
        logger.warning(
            "发送每日天尊挑战图片失败，尝试不带引用：group=%s %s: %s",
            event.group_id,
            exc.__class__.__name__,
            exc,
        )
        try:
            await daily_challenge_matcher.send(_image_message(result.image))
        except Exception as fallback_exc:  # noqa: BLE001
            logger.warning(
                "发送每日天尊挑战图片最终失败：group=%s %s: %s",
                event.group_id,
                fallback_exc.__class__.__name__,
                fallback_exc,
            )
            return

    logger.info(
        "回复每日天尊挑战：group=%s user=%s 怪物=%s 武器=%s",
        event.group_id,
        event.user_id,
        result.monster_name,
        result.weapon_name,
    )
