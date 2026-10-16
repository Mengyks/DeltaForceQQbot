"""额度记账：统计当日消耗的资源次数，超过上限主动熔断。

平台按请求次数扣"资源"，一旦失控会直接造成真金白银的损失，
因此额度检查发生在请求之前（零成本拒绝），消耗记账发生在响应之后。
"""

from __future__ import annotations

import time
from typing import Callable

from deltaforce_bot.adapters.errors import QuotaExhausted


class QuotaTracker:
    def __init__(self, daily_limit: int, time_fn: Callable[[], float] = time.time) -> None:
        self._limit = max(daily_limit, 0)
        self._time = time_fn
        self._day = self._today()
        self._spent = 0

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._time()))

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._spent = 0

    def check(self, cost: int) -> None:
        """请求前调用：超出上限则抛 QuotaExhausted。"""
        self._roll()
        if self._spent + cost > self._limit:
            raise QuotaExhausted(
                f"今日数据源资源已用尽（{self._spent}/{self._limit} 次），已暂停查询以免超额"
            )

    def spend(self, cost: int) -> None:
        self._roll()
        self._spent += max(cost, 0)

    @property
    def spent(self) -> int:
        self._roll()
        return self._spent

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def remaining(self) -> int:
        self._roll()
        return max(self._limit - self._spent, 0)
