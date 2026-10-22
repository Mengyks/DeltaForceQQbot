"""LLM 群聊机器人插件。

- 记录器（event_preprocessor）：把聊天白名单群的人类消息写入群共享上下文；
- 聊天 matcher：@/引用/叫名字立即回复；否则在机器人上次回复后的跟进窗口内回复；
- 管理 matcher：`@bot 上下文清理 提取摘要|直接清理`，仅 SUPERUSERS 可用。

只处理群消息；私聊不进入聊天。卡战备命令由 priority=10 的插件先拦截。
"""

from __future__ import annotations

import base64
import logging
import re
import time

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.message import event_preprocessor
from nonebot.rule import Rule

from deltaforce_bot.adapters.llm import LLMError
from deltaforce_bot.models.chat import ChatIncoming, IncomingImage
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.chat import (
    ChatBudgetExceeded,
    ChatEmptyContext,
    ChatRateLimited,
    ChatUnavailable,
    split_reply,
)
from deltaforce_bot.services.text import COMMAND_PATTERN

logger = logging.getLogger(__name__)

CONTEXT_COMMAND_PATTERN = re.compile(
    r"^\s*上下文清理\s*(?P<mode>提取摘要|直接清理)?\s*$",
    re.S,
)


# ---------- 事件解析 ----------
def _plaintext(event: GroupMessageEvent) -> str:
    return (event.get_plaintext() or "").strip()


def _is_command(text: str) -> bool:
    stripped = text.strip()
    return bool(
        COMMAND_PATTERN.match(stripped)
        or stripped.startswith("上下文清理")
        or CONTEXT_COMMAND_PATTERN.match(stripped)
    )


def _at_bot(event: GroupMessageEvent) -> bool:
    if getattr(event, "to_me", False):
        return True
    message = getattr(event, "original_message", None) or event.get_message()
    for segment in message:
        if segment.type == "at" and str(segment.data.get("qq")) == str(event.self_id):
            return True
    return False


def _reply_to_bot(event: GroupMessageEvent) -> bool:
    reply = getattr(event, "reply", None)
    if not reply or not getattr(reply, "sender", None):
        return False
    return str(reply.sender.user_id) == str(event.self_id)


def _nickname(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    return (
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or str(event.user_id)
    )


def _is_bot_event(event: GroupMessageEvent) -> bool:
    if str(event.get_user_id()) == str(event.self_id):
        return True
    try:
        ignore = getattr(get_runtime().settings, "chat_ignore_qq_set", set())
    except Exception:  # noqa: BLE001  运行时未初始化时按普通用户处理
        return False
    return str(event.get_user_id()) in ignore


def _is_addressed(event: GroupMessageEvent, text: str) -> bool:
    if _at_bot(event) or _reply_to_bot(event):
        return True
    try:
        aliases = getattr(get_runtime().settings, "chat_alias_list", [])
    except Exception:  # noqa: BLE001
        aliases = []
    lowered = text.lower()
    return any(alias.lower() in lowered for alias in aliases if alias)


def _image_refs(event: GroupMessageEvent) -> tuple[IncomingImage, ...]:
    """把收到的 image/mface 段转成引用；不下载，规则和记录器只关心是否存在。"""
    images: list[IncomingImage] = []
    for segment in event.get_message():
        if segment.type not in {"image", "mface"}:
            continue
        data = segment.data or {}
        images.append(
            IncomingImage(
                url=str(data.get("url") or ""),
                file=str(data.get("file") or ""),
                summary=str(data.get("summary") or ""),
            )
        )
    return tuple(images)


def _build_meta(
    event: GroupMessageEvent,
    text: str,
    images: tuple[IncomingImage, ...] = (),
) -> ChatIncoming:
    return ChatIncoming(
        group_id=event.group_id,
        user_id=event.user_id,
        nickname=_nickname(event),
        text=text,
        message_id=int(event.message_id or 0),
        timestamp=float(event.time or time.time()),
        addressed=_is_addressed(event, text),
        reply_to_bot=_reply_to_bot(event),
        images=images,
        is_command=_is_command(text),
        is_bot=_is_bot_event(event),
    )


async def _resolve_images(event: GroupMessageEvent, refs: tuple[IncomingImage, ...]) -> tuple[IncomingImage, ...]:
    """补齐没有 URL 的图片：调用 OneBot get_image 拿 base64/url。"""
    if not refs:
        return ()
    try:
        bot = nonebot.get_bot(str(event.self_id))
        max_images = int(getattr(get_runtime().settings, "chat_max_images_per_message", 4))
    except Exception:  # noqa: BLE001
        return refs

    resolved: list[IncomingImage] = []
    for image in refs[:max_images]:
        if image.url or image.data:
            resolved.append(image)
            continue
        if not image.file:
            resolved.append(image)
            continue
        if image.file.lower().startswith(("http://", "https://")):
            resolved.append(IncomingImage(url=image.file, file=image.file, summary=image.summary))
            continue
        try:
            payload = await bot.get_image(file=image.file)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_image 失败：%s: %s", exc.__class__.__name__, exc)
            resolved.append(image)
            continue
        if not isinstance(payload, dict):
            payload = {}
        url = str(payload.get("url") or "")
        b64 = payload.get("base64") or payload.get("data")
        if b64:
            try:
                encoded = str(b64)
                if encoded.startswith("data:") and "," in encoded:
                    encoded = encoded.split(",", 1)[1]
                raw = base64.b64decode(encoded)
            except Exception:  # noqa: BLE001
                raw = None
            if raw:
                resolved.append(
                    IncomingImage(
                        data=raw,
                        file=image.file,
                        summary=image.summary,
                        mime=str(payload.get("mime") or ""),
                    )
                )
                continue
        if url:
            resolved.append(IncomingImage(url=url, file=image.file, summary=image.summary))
        else:
            resolved.append(image)
    return tuple(resolved)


# ---------- 记录所有群消息 ----------
@event_preprocessor
async def record_chat_message(event: GroupMessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    try:
        runtime = get_runtime()
        chat = getattr(runtime, "chat", None)
        if chat is None or not chat.is_chat_group(event.group_id):
            return
        text = _plaintext(event)
        if _is_command(text) or _is_bot_event(event):
            return
        meta = _build_meta(event, text, _image_refs(event))
        await chat.record_message(meta)
    except Exception as exc:  # noqa: BLE001  记录失败不能影响消息处理
        logger.warning("群聊记录器失败：%s: %s", exc.__class__.__name__, exc)


# ---------- 聊天触发 ----------
def _should_chat(event: GroupMessageEvent) -> bool:
    try:
        runtime = get_runtime()
        chat = getattr(runtime, "chat", None)
        if chat is None or not chat.is_chat_group(event.group_id):
            return False
        text = _plaintext(event)
        if _is_command(text) or _is_bot_event(event):
            return False
        meta = _build_meta(event, text)
        return chat.should_respond(meta)
    except Exception:  # noqa: BLE001  规则异常时不要误回
        logger.exception("聊天触发规则异常")
        return False


chat_matcher = on_message(rule=Rule(_should_chat), priority=50, block=True)


@chat_matcher.handle()
async def handle_chat(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    chat = getattr(runtime, "chat", None)
    if chat is None:
        return

    text = _plaintext(event)
    refs = _image_refs(event)
    images = await _resolve_images(event, refs)
    meta = _build_meta(event, text, images)

    try:
        reply = await chat.handle(meta)
    except ChatRateLimited as exc:
        if meta.addressed:
            await chat_matcher.send(f"慢一点，我还在想上一句～（{max(int(exc.wait_seconds), 1)} 秒）")
        return
    except ChatBudgetExceeded:
        if chat.budget_notice_allowed(event.group_id):
            await chat_matcher.send("今天聊得有点多，先歇一会儿，明天再聊～")
        return
    except ChatUnavailable:
        if meta.addressed:
            await chat_matcher.send("聊天功能还没配置好，先找管理员看看～")
        return
    except LLMError as exc:
        logger.warning("聊天模型调用失败：%s: %s", exc.__class__.__name__, exc)
        if meta.addressed:
            await chat_matcher.send("刚才走神了，再说一次？")
        return
    except Exception as exc:  # noqa: BLE001  存储/图片等意外错误不能静默
        logger.exception("聊天处理失败：%s: %s", exc.__class__.__name__, exc)
        if meta.addressed:
            await chat_matcher.send("刚才走神了，再说一次？")
        return

    chunks = split_reply(
        reply.text,
        limit=int(getattr(runtime.settings, "chat_reply_chunk_chars", 1500)),
        max_chunks=int(getattr(runtime.settings, "chat_max_reply_chunks", 3)),
    )
    if not chunks:
        chunks = ["嗯嗯"]

    try:
        for index, chunk in enumerate(chunks):
            if index == 0:
                message = MessageSegment.reply(event.message_id) + MessageSegment.text(chunk)
            else:
                message = Message(MessageSegment.text(chunk))
            await chat_matcher.send(message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("发送聊天回复失败：%s: %s", exc.__class__.__name__, exc)
        return

    # 只在发送成功后写 assistant 消息并打开跟进窗口
    await chat.commit_reply(meta, reply.text)


# ---------- 上下文清理（仅 SUPERUSERS） ----------
def _is_context_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    text = _plaintext(event)
    if not CONTEXT_COMMAND_PATTERN.match(text):
        return False
    if not _is_addressed(event, text):
        return False
    try:
        chat = getattr(get_runtime(), "chat", None)
        return bool(chat and chat.is_chat_group(event.group_id))
    except Exception:  # noqa: BLE001
        return False


context_matcher = on_message(rule=Rule(_is_context_command), priority=9, block=True)


def _is_superuser(event: GroupMessageEvent) -> bool:
    try:
        superusers = nonebot.get_driver().config.superusers or set()
    except Exception:  # noqa: BLE001
        return False
    return str(event.get_user_id()) in {str(item) for item in superusers}


@context_matcher.handle()
async def handle_context_command(event: GroupMessageEvent) -> None:
    if not _is_superuser(event):
        await context_matcher.send("无权限：只有 SUPERUSERS 可以清理上下文")
        return

    match = CONTEXT_COMMAND_PATTERN.match(_plaintext(event))
    mode = match.group("mode") if match else None
    if not mode:
        await context_matcher.send(
            "用法：@机器人 上下文清理 提取摘要 / @机器人 上下文清理 直接清理"
        )
        return

    chat = getattr(get_runtime(), "chat", None)
    if chat is None:
        await context_matcher.send("聊天功能未启用")
        return

    try:
        if mode == "提取摘要":
            result = await chat.summarize(event.group_id)
            await context_matcher.send(
                f"已提取摘要，并清理 {result.deleted_messages} 条原始消息。"
            )
            return
        deleted = await chat.clear(event.group_id)
        await context_matcher.send(f"已直接清理上下文（{deleted} 条消息和摘要）。")
    except ChatEmptyContext:
        await context_matcher.send("当前没有可处理的聊天记录。")
    except ChatUnavailable:
        await context_matcher.send("聊天功能未配置或本群未开启。")
    except LLMError as exc:
        logger.warning("提取摘要失败：%s: %s", exc.__class__.__name__, exc)
        await context_matcher.send("摘要失败，原始记录未删除，请稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.exception("上下文操作失败：%s: %s", exc.__class__.__name__, exc)
        await context_matcher.send("操作失败，请稍后再试。")
