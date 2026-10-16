"""命令编排：解析 → 限流 → 调服务 → 格式化 → 错误映射。

抽成纯函数的好处：不用起 NoneBot 就能端到端测命令链路（见 tests/test_commands.py），
插件层只负责把返回的文本发出去。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.services.text import (
    format_custom_plan,
    format_gate_result,
    format_help,
    parse,
)

if TYPE_CHECKING:  # 仅用于类型标注，避免运行期循环导入
    from deltaforce_bot.runtime import Runtime

logger = logging.getLogger(__name__)


async def handle_gear_command(
    runtime: "Runtime",
    text: str,
    *,
    group_id: int | None = None,
    user_id: int = 0,
) -> str | None:
    """处理一条消息，返回要回复的文本；不是本插件的命令时返回 None。"""
    settings = runtime.settings
    query = parse(text, runtime.gates, custom_enabled=settings.custom_enabled)
    if query is None:
        return None

    if query.kind == "help":
        return query.error or format_help(runtime.gates, custom_enabled=settings.custom_enabled)

    if not runtime.is_debug_group(group_id):
        allowed, wait = True, 0.0
        if group_id is not None:
            allowed, wait = runtime.group_limiter.allow(f"g{group_id}")
        if allowed:
            allowed, wait = runtime.user_limiter.allow(f"u{user_id}")
        if not allowed:
            return f"查询有点频繁，{max(int(wait), 1)} 秒后再试~"

    try:
        if query.kind == "gate" and query.gate is not None:
            result = await runtime.gear_loadout.by_gate(
                query.gate,
                include_exchange=query.include_exchange,
                top=settings.alternatives_default,
            )
            return format_gate_result(result, top=settings.alternatives_default)

        if query.target is not None:
            plan = await runtime.gear_loadout.custom(
                int(query.target),
                slots=query.slots,
                exchange=query.include_exchange,
            )
            return format_custom_plan(plan, int(query.target))

        return "命令解析异常，请重新发送一次。"
    except QuotaExhausted as exc:
        logger.warning("额度熔断：%s", exc)
        return f"⚠️ {exc}"
    except DataSourceAuthError as exc:
        logger.error("数据源鉴权失败：%s", exc)
        return "数据源未配置或 token 失效，请管理员检查配置后重试。"
    except DataSourceError as exc:
        logger.warning("数据源异常：%s", exc)
        return "数据源暂时不可用，稍后再试；如果一直失败请联系管理员。"
    except ValueError as exc:
        return str(exc)
