"""带过期时间的内存缓存与并发请求合并。

设计要点：
- 平台按请求次数扣费，且明确要求"一定要缓存"，所以缓存与合并是硬性要求而非优化
- 时间源可注入，便于单测不依赖真实等待
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    """极简 TTL 缓存：(过期时间, 值)。get 返回 (是否命中, 值) 以区分缓存了 None 的情况。"""

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time = time_fn
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is None:
            return False, None
        expire_at, value = entry
        if self._time() >= expire_at:
            self._data.pop(key, None)
            return False, None
        return True, value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        self._data[key] = (self._time() + ttl, value)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class SingleFlight:
    """把同一 key 的并发请求合并成一次真实调用，其余等待者共享结果。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._tasks[key] = task
            task.add_done_callback(lambda _task, _key=key: self._tasks.pop(_key, None))
        # shield：某个等待者被取消（如用户撤回/超时）不应中断共享的请求
        return await asyncio.shield(task)

    @property
    def inflight(self) -> int:
        return len(self._tasks)
