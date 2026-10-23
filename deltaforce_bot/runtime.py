"""运行时容器：把配置、数据源客户端、业务服务、限流器装配到一起。

插件层只通过 get_runtime() 拿依赖，不自己 new 对象，方便测试替换与后续接入更多服务。
"""

from __future__ import annotations

import asyncio
import logging
import time

from deltaforce_bot.adapters.cache import SingleFlight, TTLCache
from deltaforce_bot.adapters.chat_memory import ChatMemory
from deltaforce_bot.adapters.llm import LLMClient
from deltaforce_bot.adapters.memes import MemeLibrary
from deltaforce_bot.adapters.orzice import OrziceClient
from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter
from deltaforce_bot.config import Settings, get_settings
from deltaforce_bot.models.gate import GateConfig, load_gate_config
from deltaforce_bot.render.assets import AssetLibrary
from deltaforce_bot.render.renderer import CardRenderer
from deltaforce_bot.services.chat import ChatService
from deltaforce_bot.services.gear_loadout import GearLoadoutService

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

    def is_debug_group(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.debug_groups

    async def aclose(self) -> None:
        if self.chat is not None:
            await self.chat.aclose()
        if self.chat_client is not None:
            await self.chat_client.aclose()
        if self.chat_memory is not None:
            await asyncio.to_thread(self.chat_memory.close)
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
