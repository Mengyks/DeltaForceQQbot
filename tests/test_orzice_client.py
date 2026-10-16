"""数据源客户端契约测试：缓存、合并、重试、错误映射、额度保护都不依赖真实网络。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.adapters.orzice import OrziceClient
from tests.support import load_body, make_settings

V4_PATH = "/v1/sjz_api/jzv4_zb"


def make_client(handler, **overrides):
    """构造带 MockTransport 的客户端，calls 记录每次真实 HTTP 请求。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    settings = make_settings(**overrides)
    http = httpx.AsyncClient(base_url=settings.orzice_base_url, transport=httpx.MockTransport(wrapped))
    return OrziceClient(settings, http=http), calls


async def test_success_unwraps_and_caches() -> None:
    body = load_body("jzv4_zb_lv2.json", real=True)
    client, calls = make_client(lambda request, n: httpx.Response(200, json=body))

    first = await client.get(V4_PATH, params={"lv": 0}, ttl=60, cost=1)
    second = await client.get(V4_PATH, params={"lv": 0}, ttl=60, cost=1)

    assert first == body["data"] == second
    assert len(calls) == 1  # 第二次命中缓存，没有再花钱
    assert client.requests_sent == 1
    assert client.cache_hits == 1
    assert client.quota.spent == 1
    assert calls[0].url.params["token"] == "test-token"


async def test_concurrent_identical_requests_merged() -> None:
    body = load_body("jzv4_zb_lv2.json", real=True)

    async def handler(request: httpx.Request, n: int) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=body)

    settings = make_settings()
    calls: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return await handler(request, len(calls))

    http = httpx.AsyncClient(base_url=settings.orzice_base_url, transport=httpx.MockTransport(wrapped))
    client = OrziceClient(settings, http=http)

    results = await asyncio.gather(*(client.get(V4_PATH, params={"lv": 0}, ttl=60) for _ in range(5)))

    assert len(calls) == 1  # 5 个并发请求只发一次
    assert all(result == results[0] for result in results)
    assert client.quota.spent == 1


async def test_cache_key_ignores_token() -> None:
    key = OrziceClient.cache_key(V4_PATH, {"lv": 0, "token": "secret"})
    assert key == f"{V4_PATH}?lv=0"
    assert "secret" not in key


async def test_error_code_raises_and_charges_minimal_cost() -> None:
    client, _ = make_client(lambda request, n: httpx.Response(200, json={"code": 403, "msg": "无权限访问"}))

    with pytest.raises(DataSourceError):
        await client.get(V4_PATH, ttl=60, cost=10)

    assert client.quota.spent == 1  # 失败请求按最保守方式记 1 次


async def test_http_404_treated_as_auth_error() -> None:
    client, _ = make_client(lambda request, n: httpx.Response(404, json={}))

    with pytest.raises(DataSourceAuthError):
        await client.get(V4_PATH, ttl=60)


async def test_retries_once_then_succeeds() -> None:
    body = {"code": 0, "data": {"ok": True}}
    client, calls = make_client(
        lambda request, n: httpx.Response(500) if n == 1 else httpx.Response(200, json=body)
    )

    assert await client.get(V4_PATH, ttl=60) == {"ok": True}
    assert len(calls) == 2


async def test_network_error_raises_after_retry_without_spending() -> None:
    def handler(request: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, calls = make_client(handler)

    with pytest.raises(DataSourceError):
        await client.get(V4_PATH, ttl=60)

    assert len(calls) == 2
    assert client.quota.spent == 0


async def test_quota_blocks_request_before_sending() -> None:
    client, calls = make_client(
        lambda request, n: httpx.Response(200, json={"code": 0, "data": 1}),
        orzice_daily_quota=5,
    )

    with pytest.raises(QuotaExhausted):
        await client.get(V4_PATH, ttl=60, cost=10)

    assert calls == []  # 越限时一个请求都不发


async def test_missing_token_raises_auth_error() -> None:
    client, calls = make_client(
        lambda request, n: httpx.Response(200, json={"code": 0, "data": 1}),
        orzice_token="",
    )

    with pytest.raises(DataSourceAuthError):
        await client.get(V4_PATH, ttl=60)

    assert calls == []


async def test_non_json_response_raises() -> None:
    client, _ = make_client(lambda request, n: httpx.Response(200, text="<html>not json</html>"))

    with pytest.raises(DataSourceError):
        await client.get(V4_PATH, ttl=60)


async def test_zero_ttl_disables_caching() -> None:
    body = {"code": 0, "data": 1}
    client, calls = make_client(lambda request, n: httpx.Response(200, json=body))

    await client.get(V4_PATH, ttl=0)
    await client.get(V4_PATH, ttl=0)

    assert len(calls) == 2


async def test_result_is_cached_before_flight_key_released() -> None:
    """回归测试：结果必须在并发合并的任务结束前写入缓存。

    否则在「flight 键已弹出、缓存尚未写入」的窗口里，并发请求会重复发起真实调用
    （该竞态在容器化端到端测试中实测到：3 次档位查询发出 2 次请求）。
    """
    from deltaforce_bot.adapters.cache import TTLCache

    body = {"code": 0, "data": "v"}
    client, calls = make_client(lambda request, n: httpx.Response(200, json=body))

    inflight_at_write: list[int] = []

    class ProbeCache(TTLCache):
        def set(self, key: str, value, ttl: float) -> None:
            inflight_at_write.append(client._flight.inflight)
            super().set(key, value, ttl)

    client._cache = ProbeCache()
    await client.get(V4_PATH, ttl=60)

    assert len(calls) == 1
    assert inflight_at_write == [1]  # 写缓存时合并任务仍在登记中，键尚未释放


async def test_late_duplicate_after_completion_reuses_cache() -> None:
    body = {"code": 0, "data": "v"}
    client, calls = make_client(lambda request, n: httpx.Response(200, json=body))

    await client.get(V4_PATH, ttl=60)
    await asyncio.sleep(0)  # 让 flight 键的完成回调执行
    result = await client.get(V4_PATH, ttl=60)

    assert result == "v"
    assert len(calls) == 1
