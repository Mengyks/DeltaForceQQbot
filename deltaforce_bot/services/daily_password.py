"""每日密码业务逻辑：一天只取一次、SQLite 持久缓存、查询冷却、预热/推送窗口。

设计约束（用户要求）：
- 每天北京时间 00:05 预热当天缓存，之后群友查询直接复用缓存；
- 每天北京时间 11:00 推送到白名单群；错过时间后有一小时的补推窗口；
- 同一天只向上游查询一次，成功后缓存到当天结束，后续所有请求（含定时推送）都用这份快照；
- 手动 ``/每日密码`` 每个群 5 分钟最多触发一次。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, time as time_of_day, timedelta, timezone
from typing import Callable

from deltaforce_bot.adapters.daily_password import (
    DailyPasswordClient,
    DailyPasswordError,
)
from deltaforce_bot.adapters.daily_password_store import DailyPasswordStore
from deltaforce_bot.config import Settings
from deltaforce_bot.models.daily_password import DailyPasswordSnapshot

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_TIME = "00:05"
DEFAULT_PUSH_TIME = "11:00"
_CLOCK_PATTERN = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


class DailyPasswordUnavailable(Exception):
    """当天密码暂时取不到（所有源失败或数据还没更新）。"""


class DailyPasswordCooldown(Exception):
    """保留给调用方显式抛出的冷却异常；插件通常直接看 wait_seconds。"""

    def __init__(self, wait_seconds: float) -> None:
        super().__init__(f"查询太频繁，还需等待 {format_wait(wait_seconds)}")
        self.wait_seconds = wait_seconds


class DailyPasswordService:
    def __init__(
        self,
        settings: Settings,
        client: DailyPasswordClient,
        store: DailyPasswordStore,
        *,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._store = store
        self._tz = timezone(timedelta(hours=settings.daily_password_tz_offset))
        self._now = now_fn or (lambda: datetime.now(self._tz))
        self._monotonic = monotonic_fn or time.monotonic
        self._lock = asyncio.Lock()
        self._snapshot: DailyPasswordSnapshot | None = None
        self._last_query: dict[int, float] = {}
        self._refresh_time = _parse_clock(
            settings.daily_password_refresh_time,
            DEFAULT_REFRESH_TIME,
            "DAILY_PASSWORD_REFRESH_TIME",
        )
        self._push_time = _parse_clock(
            settings.daily_password_push_time,
            DEFAULT_PUSH_TIME,
            "DAILY_PASSWORD_PUSH_TIME",
        )
        self._push_window = max(int(settings.daily_password_push_window_minutes), 0)
        self._pruned = False

    # ---------- 时间 ----------
    def now(self) -> datetime:
        current = self._now()
        if current.tzinfo is None:
            return current.replace(tzinfo=self._tz)
        return current.astimezone(self._tz)

    def today(self) -> str:
        return self.now().date().isoformat()

    @property
    def refresh_time_text(self) -> str:
        return self._refresh_time.strftime("%H:%M")

    @property
    def push_time_text(self) -> str:
        return self._push_time.strftime("%H:%M")

    def _local(self, value: datetime | None) -> datetime:
        current = value or self.now()
        if current.tzinfo is None:
            return current.replace(tzinfo=self._tz)
        return current.astimezone(self._tz)

    def in_refresh_window(self, now: datetime | None = None) -> bool:
        """从预热时间到固定推送时间之间（不含推送时刻）允许补齐当天缓存。"""
        current = self._local(now)
        start = datetime.combine(current.date(), self._refresh_time, tzinfo=self._tz)
        end = datetime.combine(current.date(), self._push_time, tzinfo=self._tz)
        if end <= start:
            return current >= start
        return start <= current < end

    def in_push_window(self, now: datetime | None = None) -> bool:
        current = self._local(now)
        start = datetime.combine(current.date(), self._push_time, tzinfo=self._tz)
        end = start + timedelta(minutes=max(self._push_window, 1))
        return start <= current < end

    # ---------- 取数（一天一次）----------
    async def get_today(self) -> DailyPasswordSnapshot:
        day = self.today()
        if self._snapshot is not None and self._snapshot.day == day:
            return self._snapshot

        async with self._lock:
            if self._snapshot is not None and self._snapshot.day == day:
                return self._snapshot

            await self._prune_once(day)

            cached = await self._read_cached(day)
            if cached is not None:
                self._snapshot = cached
                logger.info(
                    "每日密码命中当天缓存：%s（%s 条，来源 %s）",
                    day,
                    len(cached.entries),
                    cached.source,
                )
                return cached

            try:
                snapshot = await self._client.fetch(day)
            except DailyPasswordError as exc:
                raise DailyPasswordUnavailable(str(exc)) from exc

            if snapshot.day != day or not snapshot.entries:
                raise DailyPasswordUnavailable(
                    f"数据源返回的日期或内容不可用（返回 {snapshot.day}，期望 {day}）"
                )

            try:
                await asyncio.to_thread(self._store.save_snapshot, snapshot)
            except Exception as exc:  # noqa: BLE001  缓存写失败不应影响本次回复
                logger.warning("每日密码写入缓存失败：%s: %s", exc.__class__.__name__, exc)

            self._snapshot = snapshot
            logger.info(
                "每日密码获取成功：%s（%s 条，来源 %s，更新 %s）",
                day,
                len(snapshot.entries),
                snapshot.source,
                snapshot.updated_at,
            )
            return snapshot

    async def has_today_snapshot(self) -> bool:
        """当天缓存是否已就绪（内存或 SQLite）；不会触发上游请求。"""
        day = self.today()
        if self._snapshot is not None and self._snapshot.day == day:
            return True
        cached = await self._read_cached(day)
        if cached is not None:
            self._snapshot = cached
            return True
        return False

    async def _read_cached(self, day: str) -> DailyPasswordSnapshot | None:
        try:
            cached = await asyncio.to_thread(self._store.get_snapshot, day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取每日密码缓存失败：%s: %s", exc.__class__.__name__, exc)
            return None
        if cached is not None and cached.day != day:
            return None
        if cached is not None and not cached.entries:
            return None
        return cached

    async def _prune_once(self, day: str) -> None:
        if self._pruned:
            return
        self._pruned = True
        days = int(self._settings.daily_password_history_days)
        if days <= 0:
            return
        try:
            cutoff = (
                datetime.strptime(day, "%Y-%m-%d") - timedelta(days=days)
            ).date().isoformat()
            await asyncio.to_thread(self._store.prune_before, cutoff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理每日密码历史缓存失败：%s: %s", exc.__class__.__name__, exc)

    # ---------- 手动查询冷却（按群）----------
    def try_start_query(self, group_id: int) -> float:
        """返回需要等待的秒数；0 表示已记录本次触发，可以继续查询。"""
        cooldown = max(int(self._settings.daily_password_query_cooldown), 0)
        if cooldown <= 0:
            return 0.0
        now = float(self._monotonic())
        last = self._last_query.get(int(group_id))
        if last is not None:
            wait = cooldown - (now - last)
            if wait > 0:
                return wait
        self._last_query[int(group_id)] = now
        return 0.0

    # ---------- 推送去重 ----------
    async def was_pushed(self, day: str, group_id: int) -> bool:
        return bool(await asyncio.to_thread(self._store.was_pushed, day, group_id))

    async def mark_pushed(self, day: str, group_id: int) -> None:
        await asyncio.to_thread(self._store.mark_pushed, day, group_id)

    # ---------- 关闭 ----------
    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭每日密码 HTTP 客户端失败：%s: %s", exc.__class__.__name__, exc)
        try:
            await asyncio.to_thread(self._store.close)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭每日密码数据库失败：%s: %s", exc.__class__.__name__, exc)


def _parse_clock(raw: str, fallback: str, setting_name: str) -> time_of_day:
    match = _CLOCK_PATTERN.match(raw or "")
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return time_of_day(hour, minute)
    logger.warning("%s=%r 无效，回退为 %s", setting_name, raw, fallback)
    try:
        hour, minute = (int(part) for part in fallback.split(":", 1))
        return time_of_day(hour, minute)
    except (TypeError, ValueError):
        return time_of_day(0, 0)


def format_wait(seconds: float) -> str:
    total = max(int(math.ceil(seconds)), 1)
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def format_daily_password(snapshot: DailyPasswordSnapshot) -> str:
    """把快照排版成适合 QQ 的文档式纯文本。"""
    lines = ["【三角洲行动 · 每日密码】"]
    meta = f"{snapshot.day} ｜ 来源：{snapshot.source}"
    update = _format_update_time(snapshot.updated_at, snapshot.day)
    if update:
        meta += f" ｜ {update} 更新"
    lines += [meta, ""]

    for entry in snapshot.entries:
        lines.append(f"{entry.name}：{entry.code}")
        if entry.location:
            lines.append(f"  位置：{entry.location}")

    lines += [
        "",
        f"共 {len(snapshot.entries)} 张地图 ｜ 数据仅供参考，以游戏内为准",
    ]
    return "\n".join(lines)


def _format_update_time(updated_at: str, day: str) -> str:
    text = (updated_at or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.date().isoformat() == day:
        return parsed.strftime("%H:%M")
    return parsed.strftime("%m-%d %H:%M")
