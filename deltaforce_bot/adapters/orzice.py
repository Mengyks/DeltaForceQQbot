"""三角洲数据帝开放平台客户端。

职责边界：只负责"安全、经济地取回数据"，不掺业务逻辑。
- token 由本层注入查询参数，且不进入缓存键、不写日志
- 每次取数都经过：额度检查 → 缓存 → 并发合并 → 真实请求（最多重试 1 次）
- 统一把平台封装（code/message/data）剥成 data 本体，出错抛 DataSourceError
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import httpx

from deltaforce_bot.adapters.cache import SingleFlight, TTLCache
from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError
from deltaforce_bot.adapters.quota import QuotaTracker
from deltaforce_bot.config import Settings

logger = logging.getLogger(__name__)

SUCCESS_CODES = {0, 200, "0", "200"}


class OrziceClient:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            base_url=settings.orzice_base_url,
            timeout=settings.orzice_timeout,
        )
        self._cache = TTLCache()
        self._fetched_at: dict[str, float] = {}  # 键 → 本机取到数据的时间（用于展示缓存新鲜度）
        self._flight = SingleFlight()
        self.quota = QuotaTracker(settings.orzice_daily_quota)
        self.requests_sent = 0
        self.cache_hits = 0

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def cache_age(self, path: str, params: Mapping[str, Any] | None = None) -> float | None:
        """该份数据在本机缓存了多久（秒）；从未取到过则返回 None。"""
        key = self.cache_key(path, params)
        fetched = self._fetched_at.get(key)
        if fetched is None:
            return None
        return max(time.time() - fetched, 0.0)

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        ttl: int,
        cost: int = 1,
    ) -> Any:
        """取数主入口。ttl 为该数据的缓存秒数，cost 为平台一次调用的资源次数。"""
        key = self.cache_key(path, params)
        hit, value = self._cache.get(key)
        if hit:
            self.cache_hits += 1
            logger.debug("缓存命中 %s（已省 %s 次资源）", path, cost)
            return value

        self.quota.check(cost)
        query = dict(params or {})

        async def fetch_and_store() -> Any:
            # 双检：并发调用方可能在「上一个请求完成」与「结果入缓存」之间新建任务，
            # 这里再查一次缓存，避免同一秒内重复消耗额度。
            hit, cached = self._cache.get(key)
            if hit:
                return cached

            data = await self._request(path, query, cost)
            # 关键：必须在 fetch 任务结束（flight 键被释放）之前写入缓存。
            # 否则「键已弹出、缓存尚未写入」的窗口里，并发请求会重复发起真实调用
            # —— 这个问题是在容器化端到端测试里实测到的（3 次档位查询发出 2 次请求）。
            self._cache.set(key, data, ttl)
            self._fetched_at[key] = time.time()
            return data

        return await self._flight.run(key, fetch_and_store)

    @staticmethod
    def cache_key(path: str, params: Mapping[str, Any] | None) -> str:
        """缓存键只含业务参数；token 绝不入键（避免泄露到日志/调试输出）。"""
        items = sorted(
            (str(k), str(v)) for k, v in (params or {}).items() if str(k).lower() != "token"
        )
        suffix = "&".join(f"{k}={v}" for k, v in items)
        return f"{path}?{suffix}" if suffix else path

    async def _request(self, path: str, params: Mapping[str, Any], cost: int) -> Any:
        token = self._settings.orzice_token
        if not token:
            raise DataSourceAuthError("未配置数据源 token（请在 .env 填写 ORZICE_TOKEN）")

        query = {**params, "token": token}
        last_error: DataSourceError | None = None
        for attempt in (1, 2):
            try:
                response = await self._http.get(path, params=query)
            except httpx.HTTPError as exc:
                last_error = DataSourceError(f"网络异常：{exc.__class__.__name__}")
                logger.warning("请求 %s 第 %s 次失败：%s", path, attempt, exc.__class__.__name__)
                continue

            self.requests_sent += 1
            if response.status_code in (401, 403, 404):
                # 平台约定：404 即"无权限访问"
                raise DataSourceAuthError(f"数据源拒绝访问（HTTP {response.status_code}）")
            if response.status_code >= 400:
                last_error = DataSourceError(f"数据源返回 HTTP {response.status_code}")
                continue

            try:
                body = response.json()
            except ValueError as exc:
                raise DataSourceError("数据源返回内容不是合法 JSON") from exc

            data = self._unwrap(body, cost)
            self.quota.spend(cost)
            logger.info("请求 %s 成功（消耗 %s 次资源，今日累计 %s）", path, cost, self.quota.spent)
            return data

        raise last_error or DataSourceError("数据源请求失败")

    def _unwrap(self, body: Any, cost: int) -> Any:
        """剥离 code/message/data 封装，返回 data 本体。"""
        if isinstance(body, dict):
            code = body.get("code")
            if code is not None and code not in SUCCESS_CODES:
                message = body.get("message") or body.get("msg") or ""
                # 平台规则：失败请求也可能扣 1 次资源，按最保守方式记账
                self.quota.spend(min(cost, 1))
                raise DataSourceError(f"数据源返回错误 code={code} {message}".strip())
            if "data" in body:
                return body["data"]
        return body
