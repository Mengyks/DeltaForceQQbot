"""每日密码入口：``/每日密码`` 查询 + 0:05 缓存预热 + 11:00 定时推送。

- 查询：同一天只向上游取一次（服务层持久缓存），每个群 5 分钟内最多触发一次；
- 预热：每天 00:05 起确保当天缓存就绪，群友白天的查询直接命中缓存；
- 推送：每天 11:00 只推 ``DAILY_PASSWORD_GROUPS``，为空时沿用 ``CHAT_GROUPS``；同一天每群只推一次；
- 预热/推送错过时（服务重启、QQ 掉线）在各自窗口内重试。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from deltaforce_bot.models.daily_password import DailyPasswordSnapshot
from deltaforce_bot.render.daily_password_card import build_daily_password_context
from deltaforce_bot.runtime import get_runtime
from deltaforce_bot.services.daily_password import (
    DailyPasswordService,
    DailyPasswordUnavailable,
    format_daily_password,
    format_wait,
)
from deltaforce_bot.services.text import DAILY_PASSWORD_COMMAND_PATTERN

logger = logging.getLogger(__name__)

SCHEDULER_INTERVAL_SECONDS = 20
REFRESH_RETRY_SECONDS = 60
REFRESH_RETRY_WINDOW_SECONDS = 15 * 60
PUSH_FETCH_RETRY_SECONDS = 300
FETCH_WARNING_INTERVAL_SECONDS = 300


def _is_daily_password_command(event: GroupMessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return bool(DAILY_PASSWORD_COMMAND_PATTERN.match((event.get_plaintext() or "").strip()))


daily_password_matcher = on_message(
    rule=Rule(_is_daily_password_command),
    priority=10,
    block=True,
)

DAILY_PASSWORD_TEMPLATE = "daily_password.html.j2"
DAILY_PASSWORD_IMAGE_TYPE = "jpeg"
DAILY_PASSWORD_IMAGE_QUALITY = 90
DAILY_PASSWORD_IMAGE_TTL = 86400


def _image_message(image_bytes: bytes) -> Message:
    return Message(MessageSegment.image("base64://" + base64.b64encode(image_bytes).decode()))


async def _render_daily_password_image(runtime: object, snapshot: DailyPasswordSnapshot) -> bytes | None:
    """渲染当天卡片；按「日期+来源+数据更新时间」缓存，失败返回 None 走文字。"""
    renderer = getattr(runtime, "renderer", None)
    assets = getattr(runtime, "assets", None)
    if renderer is None or assets is None:
        return None

    key = f"daily-password:{snapshot.day}:{snapshot.source}:{snapshot.updated_at}"
    cache = getattr(runtime, "daily_password_image_cache", None)
    if cache is not None:
        hit, cached = cache.get(key)
        if hit:
            return cached

    try:
        service = getattr(runtime, "daily_password", None)
        push_time = getattr(service, "push_time_text", None)
        if not push_time:
            settings = getattr(runtime, "settings", None)
            push_time = (
                getattr(settings, "daily_password_push_time", "11:00")
                if settings
                else "11:00"
            )
        context = build_daily_password_context(snapshot, assets, push_time=push_time)
        image_bytes = await renderer.render_template(
            context,
            template_name=DAILY_PASSWORD_TEMPLATE,
            screenshot_type=DAILY_PASSWORD_IMAGE_TYPE,
            quality=DAILY_PASSWORD_IMAGE_QUALITY,
        )
    except Exception as exc:  # noqa: BLE001  渲染失败必须回退文字
        logger.warning(
            "每日密码卡片渲染失败，改用文字：%s: %s",
            exc.__class__.__name__,
            exc,
        )
        return None

    if cache is not None:
        cache.set(key, image_bytes, DAILY_PASSWORD_IMAGE_TTL)
    return image_bytes


@daily_password_matcher.handle()
async def handle_daily_password(event: GroupMessageEvent) -> None:
    runtime = get_runtime()
    service = getattr(runtime, "daily_password", None)
    if service is None:
        await daily_password_matcher.finish("每日密码功能未启用，请联系管理员。")

    wait = service.try_start_query(event.group_id)
    if wait > 0:
        await daily_password_matcher.finish(f"查询太频繁，{format_wait(wait)}后再试。")

    try:
        snapshot = await service.get_today()
    except DailyPasswordUnavailable as exc:
        logger.warning("每日密码获取失败：%s", exc)
        await daily_password_matcher.finish("暂时获取不到今日密码，请稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.exception("每日密码处理失败：%s: %s", exc.__class__.__name__, exc)
        await daily_password_matcher.finish("每日密码查询失败，请稍后再试。")

    text = format_daily_password(snapshot)
    image_bytes = await _render_daily_password_image(runtime, snapshot)
    if image_bytes is not None:
        try:
            await daily_password_matcher.send(_image_message(image_bytes))
        except Exception as exc:  # noqa: BLE001  图片发送失败回退文字
            logger.warning(
                "发送每日密码图片失败，改用文字：%s: %s",
                exc.__class__.__name__,
                exc,
            )
        else:
            logger.info(
                "回复每日密码查询（图片 %s KB）：group=%s 来源=%s 条数=%s",
                len(image_bytes) // 1024,
                event.group_id,
                snapshot.source,
                len(snapshot.entries),
            )
            return

    await daily_password_matcher.send(text)
    logger.info(
        "回复每日密码查询（文字）：group=%s 来源=%s 条数=%s",
        event.group_id,
        snapshot.source,
        len(snapshot.entries),
    )


# ---------- 定时任务 ----------
_refresh_task: asyncio.Task[None] | None = None
_push_task: asyncio.Task[None] | None = None
_push_error_logged: set[tuple[str, int]] = set()
_push_error_day = ""
_last_fetch_warning = 0.0
_last_push_fetch_attempt = 0.0


@dataclass
class _RefreshState:
    day: str = ""
    started_at: float | None = None
    last_attempt: float | None = None
    warning_at: float | None = None


def _warn_fetch_error(message: str) -> None:
    global _last_fetch_warning
    now = time.monotonic()
    if now - _last_fetch_warning >= FETCH_WARNING_INTERVAL_SECONDS:
        _last_fetch_warning = now
        logger.warning(message)


async def _refresh_if_needed(
    service: DailyPasswordService,
    state: _RefreshState,
    *,
    now_monotonic: float | None = None,
) -> bool:
    """在 00:05～推送时间之间确保当天缓存就绪；返回本次是否真的取了数。"""
    now = service.now()
    if not service.in_refresh_window(now):
        return False

    day = service.today()
    if day != state.day:
        state.day = day
        state.started_at = None
        state.last_attempt = None
        state.warning_at = None

    if await service.has_today_snapshot():
        return False

    mono = time.monotonic() if now_monotonic is None else now_monotonic
    if state.started_at is None:
        state.started_at = mono
    if mono - state.started_at > REFRESH_RETRY_WINDOW_SECONDS:
        return False
    if state.last_attempt is not None and mono - state.last_attempt < REFRESH_RETRY_SECONDS:
        return False
    state.last_attempt = mono

    try:
        snapshot = await service.get_today()
    except DailyPasswordUnavailable as exc:
        if state.warning_at is None or mono - state.warning_at >= FETCH_WARNING_INTERVAL_SECONDS:
            state.warning_at = mono
            logger.warning("每日密码缓存预热等待数据源（%s）：%s", day, exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("每日密码缓存预热异常：%s: %s", exc.__class__.__name__, exc)
        return False

    logger.info(
        "每日密码缓存已预热：%s（来源 %s，%s 条）",
        day,
        snapshot.source,
        len(snapshot.entries),
    )
    return True


async def _refresh_loop(service: DailyPasswordService) -> None:
    logger.info("每日密码缓存预热已启动：每天 %s 起", service.refresh_time_text)
    state = _RefreshState()
    while True:
        try:
            await _refresh_if_needed(service, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  定时任务不能因单轮异常退出
            logger.exception("每日密码预热循环异常：%s: %s", exc.__class__.__name__, exc)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def _push_once(service: DailyPasswordService, groups: set[int]) -> None:
    global _push_error_day, _last_push_fetch_attempt
    if not groups:
        return
    now = service.now()
    if not service.in_push_window(now):
        return

    day = service.today()
    if day != _push_error_day:
        _push_error_day = day
        _push_error_logged.clear()
        _last_push_fetch_attempt = 0.0

    pending: list[int] = []
    for group_id in sorted(groups):
        if not await service.was_pushed(day, group_id):
            pending.append(group_id)
    if not pending:
        return

    bots = nonebot.get_bots()
    if not bots:
        logger.debug("每日密码推送等待 bot 连接（%s）", day)
        return

    if not await service.has_today_snapshot():
        mono = time.monotonic()
        if mono - _last_push_fetch_attempt < PUSH_FETCH_RETRY_SECONDS:
            return
        _last_push_fetch_attempt = mono

    try:
        snapshot = await service.get_today()
    except DailyPasswordUnavailable as exc:
        _warn_fetch_error(f"每日密码推送暂缓（{day}）：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("每日密码推送取数异常：%s: %s", exc.__class__.__name__, exc)
        return

    runtime = get_runtime()
    bot = next(iter(bots.values()))
    text = format_daily_password(snapshot)
    image_bytes = await _render_daily_password_image(runtime, snapshot)
    image = _image_message(image_bytes) if image_bytes is not None else None

    for group_id in pending:
        sent_image = False
        sent = False
        if image is not None:
            try:
                await bot.send_group_msg(group_id=group_id, message=image)
                sent = True
                sent_image = True
            except Exception as exc:  # noqa: BLE001  图片失败不能影响文字兜底
                logger.warning(
                    "每日密码图片发送失败（群 %s），改用文字：%s: %s",
                    group_id,
                    exc.__class__.__name__,
                    exc,
                )
        if not sent:
            try:
                await bot.send_group_msg(group_id=group_id, message=text)
                sent = True
            except Exception as exc:  # noqa: BLE001  单个群失败不影响其他群，窗口内重试
                key = (day, group_id)
                if key not in _push_error_logged:
                    _push_error_logged.add(key)
                    logger.warning(
                        "每日密码推送到群 %s 失败，将在补推窗口内重试：%s: %s",
                        group_id,
                        exc.__class__.__name__,
                        exc,
                    )
                continue

        try:
            await service.mark_pushed(day, group_id)
        except Exception as exc:  # noqa: BLE001  记录失败最多导致重复推送一次
            logger.warning(
                "每日密码推送记录写入失败（群 %s）：%s: %s",
                group_id,
                exc.__class__.__name__,
                exc,
            )
        logger.info(
            "每日密码已推送到群 %s（%s，来源 %s，%s 条）",
            group_id,
            "图片" if sent_image else "文字",
            snapshot.source,
            len(snapshot.entries),
        )


async def _push_loop(service: DailyPasswordService, groups: set[int]) -> None:
    logger.info(
        "每日密码定时推送已启动：每天 %s，目标群 %s",
        service.push_time_text,
        ",".join(str(item) for item in sorted(groups)),
    )
    while True:
        try:
            await _push_once(service, groups)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  定时任务不能因单轮异常退出
            logger.exception("每日密码推送循环异常：%s: %s", exc.__class__.__name__, exc)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def _start_scheduler() -> None:
    global _refresh_task, _push_task
    runtime = get_runtime()
    service = getattr(runtime, "daily_password", None)
    if service is None:
        return
    settings = runtime.settings

    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.create_task(
            _refresh_loop(service),
            name="daily-password-refresh",
        )

    if not settings.daily_password_push_enabled:
        logger.info("每日密码定时推送已关闭（DAILY_PASSWORD_PUSH_ENABLED=false）")
        return
    groups = settings.daily_password_group_set
    if not groups:
        logger.warning(
            "每日密码定时推送未启动：没有目标群（配置 DAILY_PASSWORD_GROUPS 或 CHAT_GROUPS）"
        )
        return
    if _push_task is not None and not _push_task.done():
        return
    _push_task = asyncio.create_task(_push_loop(service, groups), name="daily-password-push")


async def _stop_scheduler() -> None:
    global _refresh_task, _push_task
    tasks = [task for task in (_refresh_task, _push_task) if task is not None]
    _refresh_task = None
    _push_task = None
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("每日密码定时任务退出异常：%s: %s", exc.__class__.__name__, exc)


_driver = nonebot.get_driver()
_driver.on_startup(_start_scheduler)
_driver.on_shutdown(_stop_scheduler)
