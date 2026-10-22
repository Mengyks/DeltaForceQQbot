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


def make_chat_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """聊天测试用的确定性配置（不读真实 .env 的聊天项）。"""
    base: dict[str, Any] = {
        "chat_enabled": True,
        "chat_groups": "30000",
        "chat_aliases": "",
        "chat_ignore_qq": "",
        "chat_db_path": tmp_path / "chat.sqlite3",
        "chat_persona_file": tmp_path / "persona.md",
        "chat_max_context_chars": 2000,
        "chat_max_input_chars": 500,
        "chat_history_ttl_days": 7,
        "chat_followup_window": 180,
        "chat_followup_max_replies": 3,
        "chat_cooldown": 0,
        "chat_ambient_enabled": False,
        "chat_ambient_cooldown": 30,
        "chat_ambient_min_chars": 2,
        "chat_rate_user": 100,
        "chat_rate_group": 100,
        "chat_daily_token_budget": 100_000,
        "chat_vision_enabled": True,
        "chat_image_max_bytes": 100_000,
        "chat_max_images_per_message": 4,
        "chat_reply_chunk_chars": 1500,
        "chat_max_reply_chunks": 3,
        "chat_concurrency": 2,
        "llm_base_url": "https://llm.test/v1",
        "llm_api_key": "test-llm-key",
        "llm_model": "test-model",
        "llm_timeout": 5.0,
        "llm_max_output_tokens": 100,
        "llm_temperature": 0.8,
        "llm_summary_max_tokens": 100,
    }
    base.update(overrides)
    return make_settings(**base)


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


class StubRenderer:
    """替代 CardRenderer：不启动浏览器，记录调用次数并可注入异常。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[object] = []

    async def render(self, context: object) -> bytes:
        self.calls.append(context)
        if self.error is not None:
            raise self.error
        return b"\x89PNG\r\n\x1a\n" + b"stub-card" * 8


class StubAssets:
    """替代 AssetLibrary：一律返回 None（走占位块渲染路径）。"""

    def item_image(self, name: str, item_type: str | None, grade: int | None):
        return None

    def background(self, map_name: str):
        return None


class StubRuntime:
    """替代 Runtime：只提供命令编排需要的接口。"""

    def __init__(
        self,
        service: Any,
        settings: Settings | None = None,
        gates: GateConfig | None = None,
        debug_groups: set[int] | None = None,
        renderer: Any = None,
    ) -> None:
        from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter

        self.settings = settings or make_settings()
        self.gates = gates or load_gates()
        self.gear_loadout = service
        self.user_limiter = SlidingWindowLimiter(self.settings.rate_limit_user)
        self.group_limiter = SlidingWindowLimiter(self.settings.rate_limit_group)
        self.debug_groups = debug_groups or set()
        from deltaforce_bot.adapters.cache import SingleFlight, TTLCache

        self.renderer = renderer if renderer is not None else StubRenderer()
        self.assets = StubAssets()
        self.image_cache: TTLCache = TTLCache()
        self.render_flight = SingleFlight()

    def is_debug_group(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.debug_groups
