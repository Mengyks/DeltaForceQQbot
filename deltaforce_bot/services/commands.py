"""命令编排：解析 → 限流 → 调服务 → 排版/渲染 → 错误映射。

抽成纯函数的好处：不用起 NoneBot 就能端到端测命令链路（见 tests/test_commands.py），
插件层只负责把返回的内容发出去（含"图片发送成功后才缓存"）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.render.card import build_card_context, card_cache_key
from deltaforce_bot.render.renderer import RenderError
from deltaforce_bot.services.text import (
    format_cache_note,
    format_custom_plan,
    format_gate_result,
    format_help,
    parse,
)

if TYPE_CHECKING:  # 仅用于类型标注，避免运行期循环导入
    from deltaforce_bot.runtime import Runtime
    from deltaforce_bot.services.gear_loadout import GateResult

logger = logging.getLogger(__name__)


@dataclass
class Reply:
    """一条回复：text 是兜底/说明文本，image 存在时优先发图片。"""

    text: str
    image: bytes | None = None
    image_cache_key: str | None = None


async def _try_render_card(runtime: "Runtime", result: "GateResult") -> tuple[bytes | None, str | None]:
    """尝试渲染图片卡片；任何失败都返回 (None, None) 让上层回退文字版。"""
    if runtime.renderer is None or result.best is None:
        return None, None

    key = card_cache_key(
        gate_label=result.gate.label,
        plan=result.best,
        updated_at=result.updated_at,
        include_exchange=result.include_exchange,
    )
    hit, cached = runtime.image_cache.get(key)
    if hit:
        logger.debug("图片缓存命中：%s", key)
        return cached, key

    async def render_once() -> bytes:
        context = build_card_context(
            plan=result.best,
            gate_label=result.gate.label,
            map_name=result.gate.map,
            difficulty=result.gate.difficulty,
            threshold=result.threshold,
            updated_at=result.updated_at,
            cache_note=format_cache_note(result.cache_age),
            alternatives=result.alternatives,
            include_exchange=result.include_exchange,
            assets=runtime.assets,
        )
        return await runtime.renderer.render(context)

    try:
        # 并发查同一档位时合并成一次渲染（缓存写入仍由插件层在"发送成功"后完成）
        image = await runtime.render_flight.run(key, render_once)
        logger.info("卡片渲染完成：%s KB（%s）", len(image) // 1024, result.gate.label)
        return image, key
    except RenderError as exc:
        logger.warning("卡片渲染失败，改用文字版：%s", exc)
        return None, None


async def handle_gear_command(
    runtime: "Runtime",
    text: str,
    *,
    group_id: int | None = None,
    user_id: int = 0,
) -> Reply | None:
    """处理一条消息，返回要回复的内容；不是本插件的命令时返回 None。"""
    settings = runtime.settings
    query = parse(text, runtime.gates, custom_enabled=settings.custom_enabled)
    if query is None:
        return None

    if query.kind == "help":
        return Reply(text=query.error or format_help(runtime.gates, custom_enabled=settings.custom_enabled))

    if not runtime.is_debug_group(group_id):
        allowed, wait = True, 0.0
        if group_id is not None:
            allowed, wait = runtime.group_limiter.allow(f"g{group_id}")
        if allowed:
            allowed, wait = runtime.user_limiter.allow(f"u{user_id}")
        if not allowed:
            return Reply(text=f"查询有点频繁，{max(int(wait), 1)} 秒后再试~")

    try:
        if query.kind == "gate" and query.gate is not None:
            result = await runtime.gear_loadout.by_gate(
                query.gate,
                include_exchange=query.include_exchange,
                top=settings.alternatives_default,
            )
            reply_text = format_gate_result(result, top=settings.alternatives_default)
            image, key = await _try_render_card(runtime, result)
            return Reply(text=reply_text, image=image, image_cache_key=key)

        if query.target is not None:
            plan = await runtime.gear_loadout.custom(
                int(query.target),
                slots=query.slots,
                exchange=query.include_exchange,
            )
            return Reply(text=format_custom_plan(plan, int(query.target)))

        return Reply(text="命令解析异常，请重新发送一次。")
    except QuotaExhausted as exc:
        logger.warning("额度熔断：%s", exc)
        return Reply(text=f"⚠️ {exc}")
    except DataSourceAuthError as exc:
        logger.error("数据源鉴权失败：%s", exc)
        return Reply(text="数据源未配置或 token 失效，请管理员检查配置后重试。")
    except DataSourceError as exc:
        logger.warning("数据源异常：%s", exc)
        return Reply(text="数据源暂时不可用，稍后再试；如果一直失败请联系管理员。")
    except ValueError as exc:
        return Reply(text=str(exc))
