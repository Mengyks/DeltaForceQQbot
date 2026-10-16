"""测试支撑代码：fixture 读取、Settings 构造、数据源/服务的测试替身。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deltaforce_bot.config import Settings
from deltaforce_bot.models.gate import GateConfig, load_gate_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_FIXTURES = FIXTURES / "real"
GATES_CONFIG = PROJECT_ROOT / "config" / "gates.json"

# 五个档位的真实门槛（来自接口返回的 zb，见 docs/02）
REAL_THRESHOLDS: dict[int, int] = {0: 112500, 1: 187500, 2: 550000, 3: 600000, 5: 780000}


def load_body(name: str, *, real: bool = False) -> dict[str, Any]:
    """读取整个响应体（含 code/message/data 封装）。"""
    base = REAL_FIXTURES if real else FIXTURES
    return json.loads((base / name).read_text(encoding="utf-8"))


def load_data(name: str, *, real: bool = False) -> Any:
    """读取响应体里的 data 部分（即客户端剥壳后业务层看到的内容）。"""
    return load_body(name, real=real)["data"]


def load_gates() -> GateConfig:
    return load_gate_config(GATES_CONFIG)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "orzice_token": "test-token",
        "orzice_base_url": "https://api.test",
        "orzice_timeout": 5.0,
        "orzice_daily_quota": 1000,
        "cache_ttl_tier": 14400,
        "cache_ttl_diy": 3600,
        "custom_enabled": False,
        "rate_limit_user": 2,
        "rate_limit_group": 10,
        "debug_groups": "",
        "alternatives_default": 3,
        "gates_config": GATES_CONFIG,
    }
    base.update(overrides)
    return Settings(**base)


class FakeClient:
    """替代 OrziceClient：不联网，返回预设 payload 并记录调用参数。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, *, params: dict[str, Any] | None = None, ttl: int, cost: int = 1) -> Any:
        self.calls.append({"path": path, "params": dict(params or {}), "ttl": ttl, "cost": cost})
        return self.payload

    def cache_age(self, path: str, params: dict[str, Any] | None = None) -> float | None:
        return None


class StubService:
    """替代 GearLoadoutService：返回预设结果或抛预设异常。"""

    def __init__(self, *, gate_result: Any = None, plan: Any = None, error: Exception | None = None) -> None:
        self.gate_result = gate_result
        self.plan = plan
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def _maybe_raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def by_gate(self, gate: Any, *, include_exchange: bool, top: int) -> Any:
        self.calls.append({"kind": "by_gate", "gate": gate, "include_exchange": include_exchange, "top": top})
        self._maybe_raise()
        return self.gate_result

    async def custom(self, target: int, *, slots: Any, exchange: bool) -> Any:
        self.calls.append({"kind": "custom", "target": target, "slots": slots, "exchange": exchange})
        self._maybe_raise()
        return self.plan


class StubRuntime:
    """替代 Runtime：只提供命令编排需要的接口。"""

    def __init__(
        self,
        service: Any,
        settings: Settings | None = None,
        gates: GateConfig | None = None,
        debug_groups: set[int] | None = None,
    ) -> None:
        from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter

        self.settings = settings or make_settings()
        self.gates = gates or load_gates()
        self.gear_loadout = service
        self.user_limiter = SlidingWindowLimiter(self.settings.rate_limit_user)
        self.group_limiter = SlidingWindowLimiter(self.settings.rate_limit_group)
        self.debug_groups = debug_groups or set()

    def is_debug_group(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.debug_groups
