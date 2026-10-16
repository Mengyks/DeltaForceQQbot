"""额度记账与限流的单元测试。"""

from __future__ import annotations

import pytest

from deltaforce_bot.adapters.errors import QuotaExhausted
from deltaforce_bot.adapters.quota import QuotaTracker
from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter


def test_check_and_spend() -> None:
    quota = QuotaTracker(daily_limit=10, time_fn=lambda: 0.0)
    quota.check(5)
    quota.spend(5)

    assert quota.spent == 5
    assert quota.remaining == 5

    with pytest.raises(QuotaExhausted):
        quota.check(6)


def test_resets_on_new_day() -> None:
    now = [0.0]
    quota = QuotaTracker(daily_limit=10, time_fn=lambda: now[0])
    quota.spend(10)

    with pytest.raises(QuotaExhausted):
        quota.check(1)

    now[0] += 86400
    quota.check(10)  # 跨天后重新计数
    assert quota.spent == 0
    assert quota.remaining == 10


def test_allows_up_to_limit_then_blocks_with_wait() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window=60, time_fn=lambda: now[0])

    assert limiter.allow("u1")[0] is True
    assert limiter.allow("u1")[0] is True

    allowed, wait = limiter.allow("u1")
    assert allowed is False
    assert 59 <= wait <= 60


def test_window_slides() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=1, window=60, time_fn=lambda: now[0])
    limiter.allow("u1")

    now[0] = 30
    assert limiter.allow("u1")[0] is False

    now[0] = 60
    assert limiter.allow("u1")[0] is True


def test_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(limit=1, time_fn=lambda: 0.0)
    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True
    assert limiter.allow("a")[0] is False


def test_zero_limit_always_blocks() -> None:
    limiter = SlidingWindowLimiter(limit=0)
    allowed, wait = limiter.allow("a")
    assert allowed is False
    assert wait == 60.0
