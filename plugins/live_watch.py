"""B站直播订阅插件：命令入口 + 开播轮询 + 卡片推送。

只做 B 站；订阅按群隔离，A 群添加/删除只影响 A 群。
轮询与推送都是独立任务，失败只记日志并退避，不影响卡战备/聊天/每日密码。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
import time
from pathlib import Path

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.adapters.live_bilibili import (
    LiveApiError,
    LiveBlockedError,
    LiveRiskControlError,
)
from deltaforce_bot.models.live import LiveNotification
from deltaforce_bot.render.live_card import build_live_card_context, render_live_card
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.live_watch import (
    LiveWatchService,
    format_live_notification_text,
    format_live_status,
    format_live_subscriptions,
)
from deltaforce_bot.services.text import (
    LIVE_COMMAND_PATTERN,
    format_live_help,
    parse_live_command,
)

logger = logging.getLogger(__name__)

POLL_START_DELAY_SECONDS = 5
POLL_ERROR_LOG_INTERVAL_SECONDS = 300
SEND_ERROR_LOG_INTERVAL_SECONDS = 300
MIN_POLL_INTERVAL_SECONDS = 60

_error_log_at: dict[str, float] = {}


def _log_throttled(
    key: str,
    message: str,
    *args: object,
    interval: float = POLL_ERROR_LOG_INTERVAL_SECONDS,
) -> None:
    now = time.monotonic()
    last = _error_log_at.get(key, 0.0)
    if now - last < interval:
        return
    _error_log_at[key] = now
    logger.warning(message, *args)


def _is_live_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return bool(LIVE_COMMAND_PATTERN.match((event.get_plaintext() or "").strip()))


live_matcher = on_message(rule=Rule(_is_live_command), priority=10, block=True)


def _is_superuser(event: GroupMessageEvent) -> bool:
    try:
        superusers = nonebot.get_driver().config.superusers or set()
    except Exception:  # noqa: BLE001
        return False
    return str(event.get_user_id()) in {str(item) for item in superusers}


def _is_group_manager(event: GroupMessageEvent) -> bool:
    """群主或群管理员都可以管理本群订阅。"""
    sender = getattr(event, "sender", None)
    return str(getattr(sender, "role", "") or "") in {"owner", "admin"}


def _rate_wait(event: GroupMessageEvent, runtime: object) -> float:
    if runtime.is_debug_group(event.group_id):
        return 0.0
    allowed, wait = runtime.live_group_limiter.allow(f"g{event.group_id}")
    if allowed:
        allowed, wait = runtime.live_user_limiter.allow(f"u{event.user_id}")
    return 0.0 if allowed else max(float(wait), 1.0)


@live_matcher.handle()
async def handle_live_command(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    service = getattr(runtime, "live", None)
    if service is None:
        await live_matcher.finish("直播订阅功能未启用。")

    groups = getattr(runtime.settings, "chat_group_set", set())
    if not groups:
        await live_matcher.finish("直播订阅未配置推送群（CHAT_GROUPS 为空）。")
    if event.group_id not in groups:
        await live_matcher.finish("本群未配置直播订阅。")

    parsed = parse_live_command(event.get_plaintext() or "")
    if parsed is None or parsed.action == "help":
        await live_matcher.finish(
            parsed.error if parsed is not None and parsed.error else format_live_help()
        )

    if parsed.action in {"add", "remove"} and not (
        _is_superuser(event) or _is_group_manager(event)
    ):
        await live_matcher.finish("只有群主、群管理员或 SUPERUSERS 可以添加/删除直播订阅。")

    if parsed.action in {"add", "remove", "query"}:
        wait = _rate_wait(event, runtime)
        if wait > 0:
            await live_matcher.finish(f"操作太频繁，{max(int(wait), 1)} 秒后再试。")

    if parsed.action == "list":
        try:
            views = await service.list_subscriptions(event.group_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取直播订阅失败：%s: %s", exc.__class__.__name__, exc)
            await live_matcher.finish("读取直播订阅失败，请稍后再试。")
        await live_matcher.finish(format_live_subscriptions(views))

    if parsed.action == "add":
        try:
            result = await service.add(
                parsed.uid,
                group_id=event.group_id,
                user_id=event.user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("添加直播订阅异常：%s: %s", exc.__class__.__name__, exc)
            await live_matcher.finish("添加订阅失败，请稍后再试。")
        await live_matcher.finish(result.message)

    if parsed.action == "remove":
        try:
            result = await service.remove(parsed.uid, group_id=event.group_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("删除直播订阅异常：%s: %s", exc.__class__.__name__, exc)
            await live_matcher.finish("删除订阅失败，请稍后再试。")
        await live_matcher.finish(result.message)

    # 剩下只有 query
    try:
        status = await service.query(parsed.uid)
    except ValueError as exc:
        await live_matcher.finish(str(exc))
    except LiveBlockedError as exc:
        logger.warning("直播查询被B站拦截：%s", exc)
        await live_matcher.finish("B站接口拒绝了请求（风控），请稍后再试。")
    except LiveRiskControlError as exc:
        logger.warning("直播查询触发B站风控：%s", exc)
        await live_matcher.finish("B站接口触发风控，请稍后再试。")
    except LiveApiError as exc:
        logger.warning("直播查询失败：%s", exc)
        await live_matcher.finish("B站接口暂时不可用，请稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.exception("直播查询异常：%s: %s", exc.__class__.__name__, exc)
        await live_matcher.finish("查询失败，请稍后再试。")

    if status is None or not status.room_id:
        await live_matcher.finish(
            f"没有找到 UID {parsed.uid} 的直播间（可能未开通直播，或 UID 有误）。"
        )
    await live_matcher.finish(format_live_status(status))


# ---------- 轮询与推送 ----------
async def _render_notification(runtime: object, notification: LiveNotification) -> bytes | None:
    renderer = getattr(runtime, "renderer", None)
    if renderer is None:
        return None
    client = getattr(runtime, "live_client", None)
    target_kb = max(int(getattr(runtime.settings, "live_image_max_kb", 350) or 350), 50)
    try:
        with tempfile.TemporaryDirectory(prefix="deltaforce-live-") as tmp:
            cover_path = None
            if client is not None and notification.cover:
                cover_path = await client.download_cover(
                    notification.cover,
                    Path(tmp) / "cover.jpg",
                )
            context = build_live_card_context(
                uname=notification.uname,
                live_status=notification.live_status,
                room_id=notification.room_id,
                cover_path=cover_path,
            )
            return await render_live_card(
                renderer,
                context,
                target_bytes=target_kb * 1024,
            )
    except Exception as exc:  # noqa: BLE001  渲染失败必须回退文字
        logger.warning(
            "直播卡片渲染失败，改用文字：%s: %s",
            exc.__class__.__name__,
            exc,
        )
        return None


def _mixed_message(image: bytes, text: str) -> Message:
    return Message(
        [
            MessageSegment.image("base64://" + base64.b64encode(image).decode()),
            MessageSegment.text("\n" + text),
        ]
    )


async def _deliver(
    service: LiveWatchService,
    notifications: list[LiveNotification],
) -> None:
    if not notifications:
        return
    bots = nonebot.get_bots()
    if not bots:
        logger.debug("直播推送等待 bot 连接：%s 条", len(notifications))
        return
    bot = next(iter(bots.values()))
    runtime = get_runtime()
    rendered: dict[tuple[str, str], bytes | None] = {}

    for notification in notifications:
        try:
            if await service.was_notified(notification):
                continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("检查直播推送记录失败：%s: %s", exc.__class__.__name__, exc)
            continue

        key = (notification.uid, notification.session_key)
        if key not in rendered:
            rendered[key] = await _render_notification(runtime, notification)
        image = rendered[key]
        text = format_live_notification_text(notification)

        sent = False
        sent_image = False
        if image is not None:
            try:
                await bot.send_group_msg(
                    group_id=notification.group_id,
                    message=_mixed_message(image, text),
                )
                sent = True
                sent_image = True
            except Exception as exc:  # noqa: BLE001  图片失败不能影响文字兜底
                _log_throttled(
                    f"send-image:{notification.uid}:{notification.group_id}:{notification.session_key}",
                    "直播卡片发送失败（群 %s，UID %s），改用文字：%s: %s",
                    notification.group_id,
                    notification.uid,
                    exc.__class__.__name__,
                    exc,
                    interval=SEND_ERROR_LOG_INTERVAL_SECONDS,
                )
        if not sent:
            try:
                await bot.send_group_msg(
                    group_id=notification.group_id,
                    message=Message(text),
                )
                sent = True
            except Exception as exc:  # noqa: BLE001  单群失败不影响其他群
                _log_throttled(
                    f"send-text:{notification.uid}:{notification.group_id}:{notification.session_key}",
                    "直播文字推送失败（群 %s，UID %s）：%s: %s",
                    notification.group_id,
                    notification.uid,
                    exc.__class__.__name__,
                    exc,
                    interval=SEND_ERROR_LOG_INTERVAL_SECONDS,
                )
                continue

        try:
            await service.mark_notified(notification)
        except Exception as exc:  # noqa: BLE001  记录失败最多导致重复推送一次
            logger.warning(
                "直播推送记录写入失败（群 %s，UID %s）：%s: %s",
                notification.group_id,
                notification.uid,
                exc.__class__.__name__,
                exc,
            )
        logger.info(
            "直播开播已推送到群 %s：UID=%s 主播=%s 图片=%s",
            notification.group_id,
            notification.uid,
            notification.display_name,
            "是" if sent_image else "否",
        )


async def _poll_loop(service: LiveWatchService, interval: int) -> None:
    logger.info("直播订阅轮询已启动：每 %s 秒检查一次", interval)
    failures = 0
    await asyncio.sleep(POLL_START_DELAY_SECONDS)
    while True:
        try:
            result = await service.poll_once()
            failures = 0
            if result.notifications:
                await _deliver(service, result.notifications)
            delay = interval
        except asyncio.CancelledError:
            raise
        except LiveBlockedError as exc:
            failures += 1
            _log_throttled(
                "poll:blocked",
                "直播轮询被B站拦截（检查 UA/机房 IP）：%s",
                exc,
                interval=600,
            )
            delay = min(interval * (2 ** min(failures, 3)), 900)
        except LiveRiskControlError as exc:
            failures += 1
            _log_throttled(
                "poll:risk",
                "直播轮询触发B站风控：%s",
                exc,
                interval=600,
            )
            delay = min(interval * (2 ** min(failures, 3)), 900)
        except LiveApiError as exc:
            failures += 1
            _log_throttled(
                f"poll:api:{str(exc)[:40]}",
                "直播轮询失败：%s",
                exc,
            )
            delay = min(interval * (2 ** min(failures, 3)), 900)
        except Exception as exc:  # noqa: BLE001  单轮异常不能让轮询退出
            failures += 1
            logger.exception("直播轮询异常：%s: %s", exc.__class__.__name__, exc)
            delay = min(interval * (2 ** min(failures, 3)), 900)
        await asyncio.sleep(delay)


_poll_task: asyncio.Task[None] | None = None


async def _start_scheduler() -> None:
    global _poll_task
    runtime = get_runtime()
    service = getattr(runtime, "live", None)
    if service is None:
        return
    if _poll_task is not None and not _poll_task.done():
        return
    try:
        pruned = await service.prune_history()
        if pruned:
            logger.info("直播推送历史已清理：%s 条", pruned)
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理直播推送历史失败：%s: %s", exc.__class__.__name__, exc)

    interval = max(
        int(getattr(runtime.settings, "live_poll_interval", 180) or 180),
        MIN_POLL_INTERVAL_SECONDS,
    )
    _poll_task = asyncio.create_task(
        _poll_loop(service, interval),
        name="live-watch-poll",
    )


async def _stop_scheduler() -> None:
    global _poll_task
    task = _poll_task
    _poll_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("直播轮询任务退出异常：%s: %s", exc.__class__.__name__, exc)


_driver = nonebot.get_driver()
_driver.on_startup(_start_scheduler)
_driver.on_shutdown(_stop_scheduler)
