"""猪结婚业务逻辑：每天每群形成互不重叠的配对。

规则：
- 发送者当天已配对：直接返回伴侣和缓存图；
- 未配对：从“未配对、非机器人、非自己”的群成员里随机选一个，
  拼图（发送者在左、被选中者在右）并写入当天配对；
- 一对形成后，两个成员都不能再被其他人选中；
- 配对按“日期 + 群号”隔离，跨天重置。
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from deltaforce_bot.adapters.waifu_avatar import WaifuAvatarClient, WaifuAvatarError
from deltaforce_bot.adapters.waifu_store import WaifuPairConflict, WaifuStore
from deltaforce_bot.models.waifu import WaifuMember, WaifuResult
from deltaforce_bot.render.waifu_card import WaifuRenderError, compose, decode_avatar

logger = logging.getLogger(__name__)

_TZ = timezone(timedelta(hours=8))


def today_utc8() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


class WaifuError(RuntimeError):
    """本功能可预期的失败（插件据此回文字提示）。"""


class WaifuUnavailable(WaifuError):
    """模板/依赖不可用。"""


class WaifuNoCandidates(WaifuError):
    """群里没有其他未配对群友可选。"""


class WaifuAvatarDownloadError(WaifuError):
    """头像下载/解码失败。"""


class WaifuService:
    def __init__(
        self,
        store: WaifuStore,
        avatar_client: WaifuAvatarClient,
        template,
        *,
        max_attempts: int = 3,
        keep_days: int = 2,
        today_fn: Callable[[], str] = today_utc8,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.avatar_client = avatar_client
        self.template = template
        self.max_attempts = max(int(max_attempts), 1)
        self.keep_days = max(int(keep_days), 1)
        self._today = today_fn
        self._rng = rng or random.Random()
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._last_prune_day: str | None = None

    @property
    def available(self) -> bool:
        return self.template is not None

    async def handle(
        self,
        *,
        group_id: int,
        sender_id: int,
        sender_name: str,
        members: Iterable[WaifuMember],
    ) -> WaifuResult:
        if not self.available:
            raise WaifuUnavailable("猪结婚模板不可用")

        day = self._today()
        await self._prune_once(day)
        async with self._lock_for(day, group_id):
            cached = await asyncio.to_thread(
                self.store.get_partner, day, group_id, sender_id
            )
            if cached is not None:
                logger.debug(
                    "猪结婚命中缓存：group=%s sender=%s partner=%s",
                    group_id,
                    sender_id,
                    cached.partner_id,
                )
                return cached

            paired = await asyncio.to_thread(
                self.store.paired_users, day, group_id
            )
            candidates = self._candidates(members, sender_id, paired)
            if not candidates:
                raise WaifuNoCandidates("没有其他未配对群友可选")

            sender_avatar = await self._fetch_avatar(sender_id)
            try:
                sender_image = await asyncio.to_thread(decode_avatar, sender_avatar)
            except WaifuRenderError as exc:
                raise WaifuAvatarDownloadError(f"发送者头像无法解析：{exc}") from exc

            self._rng.shuffle(candidates)
            last_error: Exception | None = None
            for member in candidates[: self.max_attempts]:
                try:
                    target_data = await self._fetch_avatar(member.user_id)
                    image = await asyncio.to_thread(
                        self._compose_one, sender_image, target_data
                    )
                except (WaifuAvatarDownloadError, WaifuRenderError) as exc:
                    last_error = exc
                    logger.warning(
                        "猪结婚候选 %s 处理失败，换下一个：%s",
                        member.user_id,
                        exc,
                    )
                    continue

                try:
                    await asyncio.to_thread(
                        self.store.create_pair,
                        day,
                        group_id,
                        sender_id,
                        sender_name or str(sender_id),
                        member.user_id,
                        member.name or str(member.user_id),
                        image,
                    )
                except WaifuPairConflict as exc:
                    # 并发请求抢先配对了这个成员；换下一个候选
                    last_error = exc
                    logger.debug("猪结婚并发冲突：%s", exc)
                    continue

                logger.info(
                    "猪结婚配对：group=%s %s(%s) ↔ %s(%s)",
                    group_id,
                    sender_id,
                    sender_name,
                    member.user_id,
                    member.name,
                )
                return WaifuResult(
                    partner_id=member.user_id,
                    partner_name=member.name or str(member.user_id),
                    image=image,
                )

            raise WaifuAvatarDownloadError(
                f"没有可用的头像，最后一个错误：{last_error}"
            )

    def _candidates(
        self,
        members: Iterable[WaifuMember],
        sender_id: int,
        paired: set[int],
    ) -> list[WaifuMember]:
        result: list[WaifuMember] = []
        seen: set[int] = set()
        for member in members:
            user_id = int(member.user_id)
            if user_id <= 0 or user_id == int(sender_id):
                continue
            if member.is_robot or user_id in paired or user_id in seen:
                continue
            seen.add(user_id)
            result.append(member)
        return result

    async def _fetch_avatar(self, user_id: int) -> bytes:
        try:
            return await self.avatar_client.fetch(user_id)
        except WaifuAvatarError as exc:
            raise WaifuAvatarDownloadError(
                f"下载 QQ {user_id} 头像失败：{exc}"
            ) from exc

    def _compose_one(self, sender_image, target_data: bytes) -> bytes:
        try:
            target_image = decode_avatar(target_data)
            return compose(self.template, sender_image, target_image)
        except WaifuRenderError as exc:
            raise WaifuRenderError(f"拼图失败：{exc}") from exc

    async def _prune_once(self, day: str) -> None:
        """每天第一次调用时清理过期配对；避免长期运行后 BLOB 越积越多。"""
        if self._last_prune_day == day:
            return
        try:
            cutoff = (
                datetime.strptime(day, "%Y-%m-%d")
                - timedelta(days=self.keep_days - 1)
            ).strftime("%Y-%m-%d")
        except ValueError:
            cutoff = day
        removed = await asyncio.to_thread(self.store.prune_before, cutoff)
        self._last_prune_day = day
        if removed:
            logger.info("猪结婚清理旧配对：%s 组（早于 %s）", removed, cutoff)

    def _lock_for(self, day: str, group_id: int) -> asyncio.Lock:
        key = (day, int(group_id))
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
