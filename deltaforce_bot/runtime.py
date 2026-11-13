"""运行时容器：把配置、数据源客户端、业务服务、限流器装配到一起。

插件层只通过 get_runtime() 拿依赖，不自己 new 对象，方便测试替换与后续接入更多服务。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from deltaforce_bot.adapters.cache import SingleFlight, TTLCache
from deltaforce_bot.adapters.chat_memory import ChatMemory
from deltaforce_bot.adapters.daily_challenge_assets import ChallengeAssetCatalog
from deltaforce_bot.adapters.daily_challenge_store import DailyChallengeStore
from deltaforce_bot.adapters.daily_password import DailyPasswordClient
from deltaforce_bot.adapters.daily_password_store import DailyPasswordStore
from deltaforce_bot.adapters.llm import LLMClient
from deltaforce_bot.adapters.live_bilibili import BilibiliLiveClient
from deltaforce_bot.adapters.live_store import LiveStore
from deltaforce_bot.adapters.memes import MemeLibrary
from deltaforce_bot.adapters.orzice import OrziceClient
from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter
from deltaforce_bot.adapters.waifu_avatar import WaifuAvatarClient
from deltaforce_bot.adapters.waifu_store import WaifuStore
from deltaforce_bot.config import Settings, get_settings
from deltaforce_bot.models.gate import GateConfig, load_gate_config
from deltaforce_bot.render.assets import AssetLibrary
from deltaforce_bot.render.crafting_assets import CraftingAssetLibrary
from deltaforce_bot.render.renderer import CardRenderer
from deltaforce_bot.render.waifu_card import load_template
from deltaforce_bot.services.chat import ChatService
from deltaforce_bot.services.crafting import CraftingService
from deltaforce_bot.services.daily_challenge import DailyChallengeService
from deltaforce_bot.services.daily_password import DailyPasswordService
from deltaforce_bot.services.gear_loadout import GearLoadoutService
from deltaforce_bot.services.live_watch import LiveWatchService
from deltaforce_bot.services.pig_meme import PigMemeService
from deltaforce_bot.services.waifu import WaifuService

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.gates: GateConfig = load_gate_config(settings.gates_config)
        self.client = OrziceClient(settings)
        self.gear_loadout = GearLoadoutService(self.client, settings)
        self.user_limiter = SlidingWindowLimiter(settings.rate_limit_user)
        self.group_limiter = SlidingWindowLimiter(settings.rate_limit_group)
        self.debug_groups = settings.debug_group_set
        # 图片卡片：资源索引常驻，浏览器实例按需启动（第一次渲染时）
        self.assets = AssetLibrary(settings.assets_dir)
        self.renderer = (
            CardRenderer(timeout=settings.render_timeout, chromium_path=settings.render_chromium_path)
            if settings.render_enabled
            else None
        )
        self.image_cache: TTLCache = TTLCache()  # 键→PNG，发送成功后才写入
        self.daily_password_image_cache: TTLCache = TTLCache()  # 每日密码卡片按天缓存
        self.render_flight = SingleFlight()  # 并发查同一档位时只渲染一次

        # 群聊机器人（v2）。聊天关闭时不创建数据库/客户端。
        self.chat_memory: ChatMemory | None = None
        self.chat_client: LLMClient | None = None
        self.chat: ChatService | None = None
        self.chat_user_limiter = SlidingWindowLimiter(settings.chat_rate_user)
        self.chat_group_limiter = SlidingWindowLimiter(settings.chat_rate_group)
        if settings.chat_enabled:
            try:
                self.chat_memory = ChatMemory(settings.chat_db_path)
                if settings.chat_history_ttl_days > 0:
                    cutoff = time.time() - settings.chat_history_ttl_days * 86400
                    self.chat_memory.prune_before(cutoff)
                self.chat_client = LLMClient(settings)
                meme_library = None
                if settings.chat_meme_enabled:
                    meme_library = MemeLibrary(
                        settings.chat_meme_dir,
                        max_bytes=settings.chat_meme_max_bytes,
                    )
                    if not meme_library.available:
                        logger.warning("本地表情包库为空或不可读：%s", settings.chat_meme_dir)
                self.chat = ChatService(
                    settings,
                    self.chat_client,
                    self.chat_memory,
                    self.chat_user_limiter,
                    self.chat_group_limiter,
                    memes=meme_library,
                )
                if meme_library is not None and meme_library.available:
                    logger.info(
                        "本地表情包已加载：%s 个分类、%s 张图",
                        len(meme_library.categories),
                        sum(len(category.files) for category in meme_library.categories),
                    )
            except Exception as exc:  # noqa: BLE001  聊天故障不能拖垮卡战备
                logger.warning("群聊机器人初始化失败，聊天功能关闭：%s: %s", exc.__class__.__name__, exc)
                if self.chat_memory is not None:
                    try:
                        self.chat_memory.close()
                    except Exception:  # noqa: BLE001
                        pass
                self.chat_memory = None
                self.chat_client = None
                self.chat = None

        if self.chat and self.chat.available and settings.chat_group_set:
            logger.info(
                "群聊机器人已启用：%s 个群，模型 %s",
                len(settings.chat_group_set),
                settings.llm_model,
            )
        elif settings.chat_enabled:
            logger.warning(
                "CHAT_ENABLED=true，但聊天未就绪（检查 CHAT_GROUPS / LLM_API_KEY / LLM_MODEL）"
            )
        if not settings.orzice_token:
            logger.warning("未配置 ORZICE_TOKEN，数据源请求将直接失败（请检查 .env）")

        # 每日密码（v3）：免费源 + 独立 SQLite；关闭时不创建任何对象
        self.daily_password: DailyPasswordService | None = None
        if settings.daily_password_enabled:
            daily_store: DailyPasswordStore | None = None
            try:
                daily_store = DailyPasswordStore(settings.daily_password_db_path)
                daily_client = DailyPasswordClient(settings)
                self.daily_password = DailyPasswordService(
                    settings,
                    daily_client,
                    daily_store,
                )
                logger.info(
                    "每日密码已启用：数据源 %s，预热 %s，推送 %s（UTC+%s），目标群 %s",
                    ",".join(settings.daily_password_source_list) or "未配置",
                    settings.daily_password_refresh_time,
                    settings.daily_password_push_time,
                    settings.daily_password_tz_offset,
                    ",".join(str(item) for item in sorted(settings.daily_password_group_set))
                    or "未配置",
                )
            except Exception as exc:  # noqa: BLE001  每日密码故障不能拖垮卡战备/聊天
                logger.warning(
                    "每日密码初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.daily_password = None
                if daily_store is not None:
                    try:
                        daily_store.close()
                    except Exception:  # noqa: BLE001
                        pass

        # 直播订阅（v4，B站）：全局订阅，独立 SQLite；关闭时不创建任何对象
        self.live: LiveWatchService | None = None
        self.live_client: BilibiliLiveClient | None = None
        self.live_store: LiveStore | None = None
        self.live_user_limiter = SlidingWindowLimiter(settings.live_rate_user)
        self.live_group_limiter = SlidingWindowLimiter(settings.live_rate_group)
        if settings.live_enabled:
            live_store: LiveStore | None = None
            try:
                live_store = LiveStore(settings.live_db_path)
                live_client = BilibiliLiveClient(settings)
                self.live = LiveWatchService(settings, live_client, live_store)
                self.live_client = live_client
                self.live_store = live_store
                logger.info(
                    "直播订阅已启用：B站，轮询 %s 秒，推送群 %s",
                    settings.live_poll_interval,
                    ",".join(str(item) for item in sorted(settings.chat_group_set))
                    or "未配置",
                )
                if not settings.chat_group_set:
                    logger.warning(
                        "直播订阅没有目标群（CHAT_GROUPS 为空），开播不会推送"
                    )
            except Exception as exc:  # noqa: BLE001  直播故障不能拖垮其他功能
                logger.warning(
                    "直播订阅初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.live = None
                self.live_client = None
                self.live_store = None
                if live_store is not None:
                    try:
                        live_store.close()
                    except Exception:  # noqa: BLE001
                        pass

        # 制造台利润（v5）：数据帝 Pro，按需查询，12 小时缓存；关闭时不创建对象
        self.crafting: CraftingService | None = None
        self.crafting_assets: CraftingAssetLibrary | None = None
        self.crafting_image_cache: TTLCache = TTLCache()
        if settings.crafting_enabled:
            try:
                self.crafting_assets = CraftingAssetLibrary(settings.assets_dir)
                self.crafting = CraftingService(settings, self.client)
                logger.info(
                    "制造台利润已启用：数据帝 Pro，Lv3，缓存 %s 秒",
                    settings.crafting_cache_ttl,
                )
            except Exception as exc:  # noqa: BLE001  制造台故障不能拖垮其他功能
                logger.warning(
                    "制造台利润初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.crafting = None
                self.crafting_assets = None

        # 猪猪表情包（独立功能，不依赖聊天）：精确触发词 + 按群 5 秒冷却
        self.pig_meme: PigMemeService | None = None
        if settings.pig_meme_enabled:
            try:
                self.pig_meme = PigMemeService(
                    settings.pig_meme_dir,
                    max_bytes=settings.pig_meme_max_bytes,
                    cooldown=settings.pig_meme_cooldown,
                    recent_exclude=settings.pig_meme_recent_exclude,
                )
                if self.pig_meme.available:
                    logger.info(
                        "猪猪表情包已启用：%s 张，同群冷却 %s 秒（%s）",
                        self.pig_meme.count,
                        settings.pig_meme_cooldown,
                        settings.pig_meme_dir,
                    )
                else:
                    logger.warning(
                        "猪猪表情包已启用但目录为空或不可读：%s",
                        settings.pig_meme_dir,
                    )
            except Exception as exc:  # noqa: BLE001  表情包故障不能拖垮其他功能
                logger.warning(
                    "猪猪表情包初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.pig_meme = None

        # 猪结婚（/waifu、/每天日尊挑战）：每天每群不重叠配对，SQLite 持久化
        self.waifu: WaifuService | None = None
        self.waifu_store: WaifuStore | None = None
        self.waifu_avatar: WaifuAvatarClient | None = None
        if settings.waifu_enabled:
            store: WaifuStore | None = None
            avatar: WaifuAvatarClient | None = None
            try:
                template = load_template(settings.waifu_template)
                store = WaifuStore(settings.waifu_db_path)
                cutoff = (
                    datetime.now(timezone(timedelta(hours=8)))
                    - timedelta(days=max(settings.waifu_keep_days - 1, 0))
                ).strftime("%Y-%m-%d")
                removed = store.prune_before(cutoff)
                avatar = WaifuAvatarClient(
                    timeout=settings.waifu_avatar_timeout,
                    max_bytes=settings.waifu_avatar_max_bytes,
                )
                self.waifu = WaifuService(
                    store,
                    avatar,
                    template,
                    max_attempts=settings.waifu_max_attempts,
                    keep_days=settings.waifu_keep_days,
                )
                self.waifu_store = store
                self.waifu_avatar = avatar
                logger.info(
                    "猪结婚已启用：模板 %s，保留 %s 天%s",
                    settings.waifu_template,
                    settings.waifu_keep_days,
                    f"，清理 {removed} 组旧配对" if removed else "",
                )
            except Exception as exc:  # noqa: BLE001  猪结婚故障不能拖垮其他功能
                logger.warning(
                    "猪结婚初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.waifu = None
                self.waifu_store = None
                self.waifu_avatar = None
                if store is not None:
                    try:
                        store.close()
                    except Exception:  # noqa: BLE001
                        pass

        # 每日天尊挑战（/每日天尊挑战）：随机怪物/武器 + 头像 → HTML 截图
        self.daily_challenge: DailyChallengeService | None = None
        self.daily_challenge_store: DailyChallengeStore | None = None
        self.daily_challenge_avatar: WaifuAvatarClient | None = None
        if settings.daily_challenge_enabled:
            challenge_store: DailyChallengeStore | None = None
            challenge_avatar: WaifuAvatarClient | None = None
            try:
                monsters = ChallengeAssetCatalog(
                    settings.daily_challenge_monster_dir, label="怪物"
                )
                weapons = ChallengeAssetCatalog(
                    settings.daily_challenge_weapon_dir, label="武器"
                )
                challenge_store = DailyChallengeStore(
                    settings.daily_challenge_db_path
                )
                cutoff = (
                    datetime.now(timezone(timedelta(hours=8)))
                    - timedelta(
                        days=max(settings.daily_challenge_keep_days - 1, 0)
                    )
                ).strftime("%Y-%m-%d")
                removed = challenge_store.prune_before(cutoff)
                challenge_avatar = WaifuAvatarClient(
                    timeout=settings.daily_challenge_avatar_timeout,
                    max_bytes=settings.daily_challenge_avatar_max_bytes,
                )
                self.daily_challenge = DailyChallengeService(
                    challenge_store,
                    challenge_avatar,
                    monsters,
                    weapons,
                    settings.daily_challenge_background,
                    self.renderer,
                    keep_days=settings.daily_challenge_keep_days,
                    image_max_kb=settings.daily_challenge_image_max_kb,
                )
                self.daily_challenge_store = challenge_store
                self.daily_challenge_avatar = challenge_avatar
                if self.daily_challenge.available:
                    logger.info(
                        "每日天尊挑战已启用：怪物 %s 张、武器 %s 种，缓存 %s 天%s",
                        monsters.count,
                        weapons.count,
                        settings.daily_challenge_keep_days,
                        f"，清理 {removed} 条旧缓存" if removed else "",
                    )
                else:
                    logger.warning(
                        "每日天尊挑战素材/背景/渲染器不可用，功能不会响应"
                    )
            except Exception as exc:  # noqa: BLE001  挑战故障不能拖垮其他功能
                logger.warning(
                    "每日天尊挑战初始化失败，功能关闭：%s: %s",
                    exc.__class__.__name__,
                    exc,
                )
                self.daily_challenge = None
                self.daily_challenge_store = None
                self.daily_challenge_avatar = None
                if challenge_store is not None:
                    try:
                        challenge_store.close()
                    except Exception:  # noqa: BLE001
                        pass

        if not settings.fun_group_set:
            logger.warning(
                "FUN_GROUPS 为空：猪猪表情包/猪结婚/每日天尊挑战不会响应任何群"
            )
        else:
            logger.info(
                "娱乐功能开放群：%s",
                ",".join(str(item) for item in sorted(settings.fun_group_set)),
            )

    def is_debug_group(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.debug_groups

    async def aclose(self) -> None:
        if self.chat is not None:
            await self.chat.aclose()
        if self.chat_client is not None:
            await self.chat_client.aclose()
        if self.chat_memory is not None:
            await asyncio.to_thread(self.chat_memory.close)
        if self.daily_password is not None:
            await self.daily_password.aclose()
        if self.live is not None:
            await self.live.aclose()
        else:
            if self.live_client is not None:
                await self.live_client.aclose()
            if self.live_store is not None:
                await asyncio.to_thread(self.live_store.close)
        if self.waifu_avatar is not None:
            await self.waifu_avatar.aclose()
        if self.waifu_store is not None:
            await asyncio.to_thread(self.waifu_store.close)
        if self.daily_challenge_avatar is not None:
            await self.daily_challenge_avatar.aclose()
        if self.daily_challenge_store is not None:
            await asyncio.to_thread(self.daily_challenge_store.close)
        if self.renderer is not None:
            await self.renderer.close()
        await self.client.aclose()
        logger.info(
            "数据源统计：请求 %s 次，缓存命中 %s 次，今日消耗 %s/%s 次资源",
            self.client.requests_sent,
            self.client.cache_hits,
            self.client.quota.spent,
            self.client.quota.limit,
        )


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime(get_settings())
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    """测试用：替换/重置运行时。"""
    global _runtime
    _runtime = runtime
