"""直播订阅业务层：订阅增删、批量轮询、开播通知生成。

约束（按用户确认）：
- 全局订阅：任意群添加/删除，两个群同时生效；
- 只推开播；同一场直播每个群只推一次；
- 添加时主播已在直播，则抑制当前场次，下次真正开播再推；
- 接口失败、缺数据都不能当成“下播”，状态保持不变，等下一轮。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from deltaforce_bot.adapters.live_bilibili import (
    BilibiliLiveClient,
    LiveApiError,
    LiveBlockedError,
    LiveRiskControlError,
)
from deltaforce_bot.adapters.live_store import LiveStore
from deltaforce_bot.config import Settings
from deltaforce_bot.models.live import (
    LiveNotification,
    LivePollResult,
    LiveState,
    LiveStatus,
    LiveSubscription,
)

logger = logging.getLogger(__name__)

MISSING_OFFLINE_THRESHOLD = 3
MAX_UID_LENGTH = 20
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_live_uid(raw: str) -> str:
    """校验 B 站 UID：纯数字、大于 0；同时兼容全角数字。"""
    text = (raw or "").strip().translate(_FULLWIDTH_DIGITS)
    if not text:
        raise ValueError("请给出 B 站 UID（纯数字），例如：/直播订阅 添加 672328094")
    if len(text) > MAX_UID_LENGTH or not text.isdigit():
        raise ValueError("B 站 UID 必须是纯数字")
    if int(text) <= 0:
        raise ValueError("B 站 UID 必须大于 0")
    return text


@dataclass(frozen=True)
class LiveAddResult:
    ok: bool
    message: str
    status: LiveStatus | None = None


@dataclass(frozen=True)
class LiveRemoveResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class LiveSubscriptionView:
    subscription: LiveSubscription
    state: LiveState | None

    @property
    def status(self) -> LiveStatus | None:
        return self.state.status if self.state is not None else None


class LiveWatchService:
    def __init__(
        self,
        settings: Settings,
        client: BilibiliLiveClient,
        store: LiveStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._client = client
        self._store = store
        self._clock = clock

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def target_groups(self) -> set[int]:
        return set(getattr(self._settings, "chat_group_set", set()))

    # ---------- 订阅管理 ----------
    async def add(self, raw_uid: str, *, user_id: int = 0) -> LiveAddResult:
        try:
            uid = normalize_live_uid(raw_uid)
        except ValueError as exc:
            return LiveAddResult(False, str(exc))

        existing = await asyncio.to_thread(self._store.get_subscription, uid)
        if existing is not None:
            return LiveAddResult(
                False,
                f"已经订阅过 {existing.display_name}（UID {uid}），不需要重复添加。",
            )

        limit = max(int(getattr(self._settings, "live_max_subscriptions", 50)), 1)
        count = await asyncio.to_thread(self._store.count_subscriptions)
        if count >= limit:
            return LiveAddResult(
                False,
                f"直播订阅已达上限（{limit} 个），请先删除不用的订阅。",
            )

        try:
            status = await self._client.fetch_status(uid)
        except LiveBlockedError as exc:
            logger.warning("直播订阅添加失败（B站拦截）：%s", exc)
            return LiveAddResult(False, "B站接口拒绝了请求（风控/UA），请稍后再试。")
        except LiveRiskControlError as exc:
            logger.warning("直播订阅添加失败（B站风控）：%s", exc)
            return LiveAddResult(False, "B站接口触发风控，请稍后再试。")
        except LiveApiError as exc:
            logger.warning("直播订阅添加失败（B站接口）：%s", exc)
            return LiveAddResult(False, "B站接口暂时不可用，请稍后再试。")
        except Exception as exc:  # noqa: BLE001  添加失败不能影响其他功能
            logger.exception("直播订阅添加异常：%s: %s", exc.__class__.__name__, exc)
            return LiveAddResult(False, "添加订阅失败，请稍后再试。")

        if status is None or not status.room_id:
            return LiveAddResult(
                False,
                f"没有找到 UID {uid} 的直播间（可能未开通直播，或 UID 有误）。",
            )

        now = self._clock()
        subscription = LiveSubscription(
            uid=status.uid or uid,
            room_id=status.room_id,
            uname=status.uname,
            added_by=int(user_id or 0),
            added_at=now,
        )
        inserted = await asyncio.to_thread(self._store.add_subscription, subscription)
        if not inserted:
            return LiveAddResult(False, f"已经订阅过 UID {uid}。")

        await asyncio.to_thread(
            self._store.upsert_state,
            LiveState(status=status, checked_at=now),
        )

        groups = self.target_groups
        if status.is_live:
            for group_id in groups:
                await asyncio.to_thread(
                    self._store.mark_notification,
                    status.uid or uid,
                    group_id,
                    status.session_key,
                    kind="suppress",
                    notified_at=now,
                )
            logger.info(
                "直播订阅已添加：UID=%s 主播=%s；添加时正在直播，已抑制当前场次",
                subscription.uid,
                subscription.uname or "-",
            )
            message = (
                f"已订阅 {subscription.display_name}（UID {subscription.uid}，房间 {subscription.room_id}）。"
                "当前正在直播，本次不推送；下次开播会提醒已配置的群。"
            )
        else:
            logger.info(
                "直播订阅已添加：UID=%s 主播=%s；当前%s",
                subscription.uid,
                subscription.uname or "-",
                status.status_text,
            )
            message = (
                f"已订阅 {subscription.display_name}（UID {subscription.uid}，房间 {subscription.room_id}）。"
                f"当前{status.status_text}，开播后会提醒已配置的群。"
            )

        if not groups:
            message += "\n注意：当前 CHAT_GROUPS 为空，开播后不会推送。"
        return LiveAddResult(True, message, status)

    async def remove(self, raw_uid: str) -> LiveRemoveResult:
        try:
            uid = normalize_live_uid(raw_uid)
        except ValueError as exc:
            return LiveRemoveResult(False, str(exc))

        subscription = await asyncio.to_thread(self._store.get_subscription, uid)
        if subscription is None:
            return LiveRemoveResult(False, f"没有找到 UID {uid} 的直播订阅。")

        await asyncio.to_thread(self._store.remove_subscription, uid)
        await asyncio.to_thread(self._store.delete_state, uid)
        logger.info("直播订阅已删除：UID=%s 主播=%s", uid, subscription.uname or "-")
        return LiveRemoveResult(
            True,
            f"已取消订阅 {subscription.display_name}（UID {uid}），之后不再推送。",
        )

    async def query(self, raw_uid: str) -> LiveStatus | None:
        uid = normalize_live_uid(raw_uid)
        return await self._client.fetch_status(uid)

    async def list_subscriptions(self) -> list[LiveSubscriptionView]:
        subscriptions = await asyncio.to_thread(self._store.list_subscriptions)
        states = await asyncio.to_thread(self._store.list_states)
        return [
            LiveSubscriptionView(subscription=item, state=states.get(item.uid))
            for item in subscriptions
        ]

    # ---------- 轮询 ----------
    async def poll_once(self) -> LivePollResult:
        now = self._clock()
        subscriptions = await asyncio.to_thread(self._store.list_subscriptions)
        result = LivePollResult(checked=len(subscriptions))
        if not subscriptions:
            return result

        statuses = await self._client.fetch_statuses([item.uid for item in subscriptions])
        states = await asyncio.to_thread(self._store.list_states)
        groups = self.target_groups

        for subscription in subscriptions:
            uid = subscription.uid
            status = statuses.get(uid)
            previous = states.get(uid)

            if status is None:
                result.missing.append(uid)
                await self._handle_missing(subscription, previous, now)
                continue

            if previous is not None and (
                previous.status.room_id != status.room_id
                or previous.status.uname != status.uname
            ):
                await asyncio.to_thread(
                    self._store.update_subscription_meta,
                    uid,
                    room_id=status.room_id,
                    uname=status.uname,
                )

            await asyncio.to_thread(
                self._store.upsert_state,
                LiveState(status=status, checked_at=now, missing_count=0),
            )
            if not status.is_live:
                if status.session_key == "live":
                    await asyncio.to_thread(
                        self._store.delete_notifications_for_session, uid, "live"
                    )
                continue

            result.live += 1
            if previous is None:
                # 首次观察（例如手工写入/旧数据迁移）不补推，避免启动时刷屏。
                # 同时写入抑制记录，否则下一轮会把它当成“开播转换”补推。
                for group_id in groups:
                    await asyncio.to_thread(
                        self._store.mark_notification,
                        uid,
                        group_id,
                        status.session_key,
                        kind="suppress",
                        notified_at=now,
                    )
                logger.info("首次观察 UID=%s 正在直播，不补推", uid)
                continue
            if previous.is_live and previous.status.session_key != status.session_key:
                # 仍在直播但 live_time 变了（断流重连等）：按同一场处理，避免重复推送。
                for group_id in groups:
                    await asyncio.to_thread(
                        self._store.mark_notification,
                        uid,
                        group_id,
                        status.session_key,
                        kind="suppress",
                        notified_at=now,
                    )
                logger.info("UID=%s 直播场次标识变化但仍在直播，抑制新场次", uid)
                continue

            for group_id in sorted(groups):
                if await asyncio.to_thread(
                    self._store.was_notified, uid, group_id, status.session_key
                ):
                    continue
                result.notifications.append(
                    LiveNotification(
                        uid=uid,
                        group_id=group_id,
                        session_key=status.session_key,
                        uname=status.uname or subscription.uname,
                        room_id=status.room_id or subscription.room_id,
                        title=status.title,
                        cover=status.cover,
                        live_time=status.live_time,
                        live_status=status.live_status,
                    )
                )

        logger.debug(
            "直播轮询完成：订阅 %s，直播中 %s，待推送 %s，缺失 %s",
            result.checked,
            result.live,
            len(result.notifications),
            len(result.missing),
        )
        return result

    async def _handle_missing(
        self,
        subscription: LiveSubscription,
        previous: LiveState | None,
        now: float,
    ) -> None:
        missing_count = (previous.missing_count if previous is not None else 0) + 1
        if previous is None:
            state = LiveState(
                status=LiveStatus(
                    uid=subscription.uid,
                    room_id=subscription.room_id,
                    uname=subscription.uname,
                ),
                checked_at=now,
                missing_count=missing_count,
            )
        elif missing_count >= MISSING_OFFLINE_THRESHOLD and previous.is_live:
            offline = LiveStatus(
                uid=previous.status.uid,
                room_id=previous.status.room_id,
                uname=previous.status.uname,
                live_status=0,
                live_time=0,
                title=previous.status.title,
                cover=previous.status.cover,
            )
            state = LiveState(status=offline, checked_at=now, missing_count=missing_count)
            if previous.status.session_key == "live":
                await asyncio.to_thread(
                    self._store.delete_notifications_for_session,
                    subscription.uid,
                    "live",
                )
            logger.warning(
                "UID=%s 连续 %s 次未返回直播状态，按未开播处理",
                subscription.uid,
                missing_count,
            )
        else:
            state = LiveState(
                status=previous.status,
                checked_at=now,
                missing_count=missing_count,
            )
        await asyncio.to_thread(self._store.upsert_state, state)

    async def mark_notified(self, notification: LiveNotification) -> None:
        await asyncio.to_thread(
            self._store.mark_notification,
            notification.uid,
            notification.group_id,
            notification.session_key,
            kind="push",
            notified_at=self._clock(),
        )

    async def was_notified(self, notification: LiveNotification) -> bool:
        return bool(
            await asyncio.to_thread(
                self._store.was_notified,
                notification.uid,
                notification.group_id,
                notification.session_key,
            )
        )

    async def prune_history(self) -> int:
        days = int(getattr(self._settings, "live_history_days", 30))
        if days <= 0:
            return 0
        cutoff = self._clock() - days * 86400
        return int(await asyncio.to_thread(self._store.prune_notifications_before, cutoff))

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 B站直播客户端失败：%s: %s", exc.__class__.__name__, exc)
        try:
            await asyncio.to_thread(self._store.close)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭直播订阅数据库失败：%s: %s", exc.__class__.__name__, exc)


# ---------- 纯文本排版 ----------
def format_live_status(status: LiveStatus) -> str:
    lines = [f"【B站直播】{status.uname or status.uid}（UID {status.uid}）"]
    lines.append(f"状态：{status.status_text}")
    if status.title:
        lines.append(f"标题：{status.title[:80]}")
    if status.room_url:
        lines.append(f"直播间：{status.room_url}")
    else:
        lines.append("没有找到该 UID 的直播间。")
    return "\n".join(lines)


def format_live_subscriptions(views: list[LiveSubscriptionView]) -> str:
    if not views:
        return "当前没有直播订阅。\n发送 /直播订阅 添加 <B站UID> 添加一个。"
    lines = [f"全局直播订阅（两个群共同，共 {len(views)} 个）："]
    for index, view in enumerate(views[:30], start=1):
        item = view.subscription
        state = view.state
        status_text = state.status.status_text if state is not None else "尚未检查"
        line = f"{index}. {item.display_name}（UID {item.uid}）— {status_text}"
        if state is not None and state.status.is_live and state.status.title:
            line += f"：{state.status.title[:40]}"
        lines.append(line)
    if len(views) > 30:
        lines.append(f"……还有 {len(views) - 30} 个未显示。")
    return "\n".join(lines)


def format_live_notification_text(notification: LiveNotification) -> str:
    lines = [f"【B站开播】{notification.display_name}"]
    if notification.title:
        lines.append(notification.title[:80])
    if notification.room_url:
        lines.append(f"直播间：{notification.room_url}")
    return "\n".join(lines)
