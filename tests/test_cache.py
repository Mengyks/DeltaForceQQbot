"""缓存与并发合并的单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from deltaforce_bot.adapters.cache import SingleFlight, TTLCache


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_miss_then_hit() -> None:
    cache = TTLCache(time_fn=FakeClock())
    assert cache.get("k") == (False, None)
    cache.set("k", {"v": 1}, ttl=10)
    assert cache.get("k") == (True, {"v": 1})


def test_expires_at_ttl() -> None:
    clock = FakeClock()
    cache = TTLCache(time_fn=clock)
    cache.set("k", 1, ttl=10)

    clock.now = 9.99
    assert cache.get("k")[0] is True

    clock.now = 10.0
    assert cache.get("k")[0] is False
    assert len(cache) == 0


def test_can_cache_none_value() -> None:
    cache = TTLCache()
    cache.set("k", None, ttl=5)
    assert cache.get("k") == (True, None)


def test_non_positive_ttl_is_not_cached() -> None:
    cache = TTLCache()
    cache.set("k", 1, ttl=0)
    assert cache.get("k")[0] is False


async def test_single_flight_merges_concurrent_calls() -> None:
    flight = SingleFlight()
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    results = await asyncio.gather(*(flight.run("k", factory) for _ in range(5)))
    await asyncio.sleep(0)  # 让 done_callback 执行

    assert results == ["value"] * 5
    assert calls == 1
    assert flight.inflight == 0


async def test_single_flight_failure_is_not_shared_forever() -> None:
    flight = SingleFlight()
    attempts = 0

    async def failing() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await flight.run("k", failing)
    with pytest.raises(RuntimeError):
        await flight.run("k", failing)

    assert attempts == 2  # 失败不入缓存，允许下一次重新尝试
