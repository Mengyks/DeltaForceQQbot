"""滑动窗口限流（按用户 / 按群）。"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable


class SlidingWindowLimiter:
    """在 window 秒内最多允许 limit 次；返回 (是否允许, 还需等待秒数)。"""

    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = max(limit, 0)
        self._window = window
        self._time = time_fn
        self._buckets: dict[str, deque[float]] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        if self._limit <= 0:
            return False, self._window
        now = self._time()
        bucket = self._buckets.setdefault(key, deque())
        while bucket and now - bucket[0] >= self._window:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return False, self._window - (now - bucket[0])
        bucket.append(now)
        return True, 0.0
