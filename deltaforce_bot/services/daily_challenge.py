"""每日天尊挑战业务逻辑：随机怪物 + 随机武器 + 发送者头像 → HTML 截图。"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from deltaforce_bot.adapters.daily_challenge_assets import ChallengeAssetCatalog
from deltaforce_bot.adapters.daily_challenge_store import DailyChallengeStore
from deltaforce_bot.adapters.waifu_avatar import WaifuAvatarClient, WaifuAvatarError
from deltaforce_bot.models.daily_challenge import DailyChallengeResult
from deltaforce_bot.render.daily_challenge_card import (
    build_daily_challenge_context,
    render_daily_challenge,
)

logger = logging.getLogger(__name__)

_TZ = timezone(timedelta(hours=8))


def today_utc8() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


class DailyChallengeError(RuntimeError):
    """本功能可预期的失败。"""


class DailyChallengeUnavailable(DailyChallengeError):
    """图库/背景/渲染器不可用。"""


class DailyChallengeAvatarError(DailyChallengeError):
    """头像下载失败。"""


class DailyChallengeRenderError(DailyChallengeError):
    """HTML 截图失败或压缩后仍然过大。"""


class DailyChallengeService:
    def __init__(
        self,
        store: DailyChallengeStore,
        avatar_client: WaifuAvatarClient,
        monsters: ChallengeAssetCatalog,
        weapons: ChallengeAssetCatalog,
        background: Path,
        renderer,
        *,
        keep_days: int = 2,
        image_max_kb: int = 350,
        today_fn: Callable[[], str] = today_utc8,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.avatar_client = avatar_client
        self.monsters = monsters
        self.weapons = weapons
        self.background = Path(background).resolve()
        self.renderer = renderer
        self.keep_days = max(int(keep_days), 1)
        self.image_max_kb = max(int(image_max_kb), 64)
        self._today = today_fn
        self._rng = rng or random.Random()
        self._locks: dict[tuple[str, int, int], asyncio.Lock] = {}
        self._last_prune_day: str | None = None
        self._available = bool(
            monsters.available
            and weapons.available
            and self.background.exists()
            and renderer is not None
        )
        if not self._available:
            logger.warning(
                "每日天尊挑战不可用：怪物 %s 张、武器 %s 种、背景存在=%s、渲染器=%s",
                monsters.count,
                weapons.count,
                self.background.exists(),
                renderer is not None,
            )

    @property
    def available(self) -> bool:
        return self._available

    async def handle(
        self,
        *,
        group_id: int,
        user_id: int,
        hunter_name: str,
    ) -> DailyChallengeResult:
        if not self._available:
            raise DailyChallengeUnavailable("每日天尊挑战素材或渲染器不可用")

        day = self._today()
        await self._prune_once(day)
        async with self._lock_for(day, group_id, user_id):
            cached = await asyncio.to_thread(
                self.store.get, day, group_id, user_id
            )
            if cached is not None:
                logger.debug(
                    "每日天尊挑战命中缓存：group=%s user=%s", group_id, user_id
                )
                return cached

            monster = self.monsters.pick(self._rng)
            weapon = self.weapons.pick(self._rng)
            if monster is None or weapon is None:
                raise DailyChallengeUnavailable("怪物或武器图库为空")

            avatar = await self._fetch_avatar(user_id)
            context = build_daily_challenge_context(
                hunter_name=hunter_name or str(user_id),
                monster=monster,
                weapon=weapon,
                background=self.background,
                avatar_data=avatar,
            )
            image = await render_daily_challenge(
                self.renderer,
                context,
                target_bytes=self.image_max_kb * 1024,
            )
            if image is None:
                raise DailyChallengeRenderError("每日天尊挑战截图失败")

            await asyncio.to_thread(
                self.store.save,
                day,
                group_id,
                user_id,
                monster.name,
                weapon.name,
                image,
            )
            logger.info(
                "每日天尊挑战生成：group=%s user=%s 怪物=%s 武器=%s（%s KB）",
                group_id,
                user_id,
                monster.name,
                weapon.name,
                len(image) // 1024,
            )
            return DailyChallengeResult(
                monster_name=monster.name,
                weapon_name=weapon.name,
                image=image,
            )

    async def _fetch_avatar(self, user_id: int) -> bytes:
        try:
            return await self.avatar_client.fetch(user_id)
        except WaifuAvatarError as exc:
            raise DailyChallengeAvatarError(
                f"下载 QQ {user_id} 头像失败：{exc}"
            ) from exc

    async def _prune_once(self, day: str) -> None:
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
            logger.info("每日天尊挑战清理旧缓存：%s 条（早于 %s）", removed, cutoff)

    def _lock_for(self, day: str, group_id: int, user_id: int) -> asyncio.Lock:
        key = (day, int(group_id), int(user_id))
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
