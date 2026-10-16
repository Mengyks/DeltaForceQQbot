"""运行时容器：把配置、数据源客户端、业务服务、限流器装配到一起。

插件层只通过 get_runtime() 拿依赖，不自己 new 对象，方便测试替换与后续接入更多服务。
"""

from __future__ import annotations

import logging

from deltaforce_bot.adapters.orzice import OrziceClient
from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter
from deltaforce_bot.config import Settings, get_settings
from deltaforce_bot.models.gate import GateConfig, load_gate_config
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
        if not settings.orzice_token:
            logger.warning("未配置 ORZICE_TOKEN，数据源请求将直接失败（请检查 .env）")

    def is_debug_group(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.debug_groups

    async def aclose(self) -> None:
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
