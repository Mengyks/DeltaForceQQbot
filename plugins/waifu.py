"""猪结婚插件：``/waifu`` 或 ``/每天日尊挑战``。

每天每群形成互不重叠的配对；发送者当天已配对时直接返回伴侣。
只处理群消息，独立于聊天开关与群白名单。
"""

from __future__ import annotations

import base64
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.models.waifu import WaifuMember
from deltaforce_bot.render.waifu_card import WaifuRenderError
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.text import WAIFU_COMMAND_PATTERN
from deltaforce_bot.services.waifu import (
    WaifuAvatarDownloadError,
    WaifuError,
    WaifuNoCandidates,
    WaifuUnavailable,
)

logger = logging.getLogger(__name__)


def _is_waifu_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not WAIFU_COMMAND_PATTERN.match(event.get_plaintext() or ""):
        return False
    try:
        settings = get_runtime().settings
    except Exception:  # noqa: BLE001  运行时未初始化时不触发
        return False
    return event.group_id in settings.fun_group_set


waifu_matcher = on_message(rule=Rule(_is_waifu_command), priority=10, block=True)


def _name_of(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    return str(
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or event.user_id
    )


def _normalize_members(
    raw_members: list[dict],
    *,
    self_id: int,
    ignore_ids: set[str],
) -> list[WaifuMember]:
    members: list[WaifuMember] = []
    seen: set[int] = set()
    for item in raw_members:
        try:
            user_id = int(item.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        if user_id <= 0 or user_id in seen:
            continue
        name = str(item.get("card") or item.get("nickname") or user_id).strip()
        is_robot = bool(item.get("is_robot")) or str(item.get("role") or "").lower() == "bot"
        if user_id == int(self_id) or str(user_id) in ignore_ids:
            is_robot = True
        seen.add(user_id)
        members.append(
            WaifuMember(
                user_id=user_id,
                name=name or str(user_id),
                is_robot=is_robot,
            )
        )
    return members


def _build_message(event: GroupMessageEvent, result) -> Message:
    image = MessageSegment.image(
        "base64://" + base64.b64encode(result.image).decode()
    )
    text = MessageSegment.text(f"你今天的老婆是{result.partner_name}")
    segments: list[MessageSegment] = []
    if event.message_id:
        segments.append(MessageSegment.reply(event.message_id))
    segments.extend((image, text))
    return Message(segments)


def _build_fallback(result) -> Message:
    return Message(
        [
            MessageSegment.image("base64://" + base64.b64encode(result.image).decode()),
            MessageSegment.text(f"你今天的老婆是{result.partner_name}"),
        ]
    )


@waifu_matcher.handle()
async def handle_waifu(bot: Bot, event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    settings = runtime.settings

    # 机器人自己/其他已知机器人发的不处理，避免互刷
    if str(event.user_id) == str(event.self_id) or str(event.user_id) in settings.chat_ignore_qq_set:
        return

    service = getattr(runtime, "waifu", None)
    if service is None or not service.available:
        await waifu_matcher.finish("猪结婚功能还没准备好，先找管理员看看～")
        return

    try:
        raw_members = await bot.get_group_member_list(group_id=event.group_id)
    except Exception as exc:  # noqa: BLE001  群成员接口失败不能影响其他功能
        logger.warning("获取群成员失败：%s: %s", exc.__class__.__name__, exc)
        await waifu_matcher.finish("获取群成员失败，稍后再试～")
        return

    members = _normalize_members(
        raw_members or [],
        self_id=event.self_id,
        ignore_ids=settings.chat_ignore_qq_set,
    )

    try:
        result = await service.handle(
            group_id=event.group_id,
            sender_id=event.user_id,
            sender_name=_name_of(event),
            members=members,
        )
    except WaifuNoCandidates:
        await waifu_matcher.finish("群里没有其他群友可选～")
        return
    except WaifuUnavailable:
        await waifu_matcher.finish("猪结婚功能还没准备好，先找管理员看看～")
        return
    except (WaifuAvatarDownloadError, WaifuRenderError) as exc:
        logger.warning("猪结婚拼图失败：group=%s %s", event.group_id, exc)
        await waifu_matcher.finish("这次没拼好，稍后再试～")
        return
    except WaifuError as exc:
        logger.warning("猪结婚失败：group=%s %s", event.group_id, exc)
        await waifu_matcher.finish("结婚功能出了点问题，稍后再试～")
        return
    except Exception:  # noqa: BLE001
        logger.exception("猪结婚未预期错误：group=%s", event.group_id)
        await waifu_matcher.finish("结婚功能出了点问题，稍后再试～")
        return

    message = _build_message(event, result)
    try:
        await waifu_matcher.send(message)
    except Exception as exc:  # noqa: BLE001  引用回复失败时退回普通图片+文字
        logger.warning(
            "发送猪结婚图片失败，尝试不带引用：group=%s %s: %s",
            event.group_id,
            exc.__class__.__name__,
            exc,
        )
        fallback = _build_fallback(result)
        try:
            await waifu_matcher.send(fallback)
        except Exception as fallback_exc:  # noqa: BLE001
            logger.warning(
                "发送猪结婚图片最终失败：group=%s %s: %s",
                event.group_id,
                fallback_exc.__class__.__name__,
                fallback_exc,
            )
            return

    logger.info(
        "回复猪结婚：group=%s sender=%s partner=%s(%s)",
        event.group_id,
        event.user_id,
        result.partner_id,
        result.partner_name,
    )
