"""命令编排测试：从文本到回复内容（文字/图片）的完整链路，不联网、不起框架。"""

from __future__ import annotations

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.models.loadout import LoadoutPlan
from deltaforce_bot.render.card import card_cache_key
from deltaforce_bot.render.renderer import RenderError
from deltaforce_bot.services.commands import handle_gear_command
from deltaforce_bot.services.gear_loadout import GateResult, GearLoadoutService
from tests.support import (
    FakeClient,
    StubRuntime,
    StubService,
    StubRenderer,
    load_data,
    load_gates,
    make_settings,
)

GATES = load_gates()


def gate(lv: int):
    return next(item for item in GATES.gates if item.lv == lv)


async def build_result(*, include_exchange: bool = False) -> GateResult:
    service = GearLoadoutService(FakeClient(load_data("jzv4_zb_lv2.json", real=True)), make_settings())
    return await service.by_gate(gate(2), include_exchange=include_exchange, top=3)


async def test_gate_command_end_to_end() -> None:
    service = StubService(gate_result=await build_result())
    reply = await handle_gear_command(StubRuntime(service), "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None
    assert "巴克什 绝密" in reply.text
    assert "门槛 55万" in reply.text
    assert service.calls[0]["gate"].lv == 2
    assert service.calls[0]["include_exchange"] is False
    assert service.calls[0]["top"] == 3


async def test_gate_command_attaches_rendered_card() -> None:
    """正常情况：文字兜底 + 渲染好的图片 + 图片缓存键一起返回。"""
    result = await build_result()
    service = StubService(gate_result=result)
    renderer = StubRenderer()
    runtime = StubRuntime(service, renderer=renderer)

    reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None
    assert reply.image is not None and reply.image.startswith(b"\x89PNG")
    assert reply.image_cache_key is not None
    assert len(renderer.calls) == 1  # 渲染了一次


async def test_render_failure_falls_back_to_text() -> None:
    """渲染失败必须降级为文字版，不能影响命令可用性。"""
    service = StubService(gate_result=await build_result())
    renderer = StubRenderer(error=RenderError("浏览器炸了"))
    runtime = StubRuntime(service, renderer=renderer)

    reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None
    assert reply.image is None
    assert reply.image_cache_key is None
    assert "巴克什 绝密" in reply.text  # 文字版照发


async def test_image_cache_hit_skips_rendering() -> None:
    """图片缓存命中时不再渲染（4 小时内复用同一张卡片）。"""
    result = await build_result()
    service = StubService(gate_result=result)
    renderer = StubRenderer()
    runtime = StubRuntime(service, renderer=renderer)

    key = card_cache_key(
        gate_label=result.gate.label,
        plan=result.best,
        updated_at=result.updated_at,
        include_exchange=result.include_exchange,
    )
    runtime.image_cache.set(key, b"\x89PNG-cached", ttl=60)

    reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None
    assert reply.image == b"\x89PNG-cached"
    assert renderer.calls == []  # 没有触发渲染


async def test_render_disabled_returns_text_only() -> None:
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=make_settings(render_enabled=False), renderer=StubRenderer())
    runtime.renderer = None  # 关掉渲染时的等价状态

    reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None and reply.image is None


async def test_alias_commands() -> None:
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service)

    assert (await handle_gear_command(runtime, "/卡战备 集市 绝密", group_id=1, user_id=11)).text
    assert service.calls[-1]["gate"].lv == 2

    assert (await handle_gear_command(runtime, "/卡战备 航天基地 绝密", group_id=1, user_id=12)).text
    assert service.calls[-1]["gate"].lv == 3

    assert (await handle_gear_command(runtime, "/卡战备 航天 绝密 兑换", group_id=1, user_id=13)).text
    assert service.calls[-1]["gate"].lv == 3
    assert service.calls[-1]["include_exchange"] is True


async def test_help_and_unsupported_requests() -> None:
    runtime = StubRuntime(StubService())

    assert await handle_gear_command(runtime, "晚上吃什么", group_id=1, user_id=2) is None

    for text, needle in (
        ("/卡战备", "请给出地图和难度"),
        ("/卡战备 巴克什", "需要指定难度"),
        ("/卡战备 巴克什 常规", "常规模式不需要战备值"),
        ("/卡战备 大坝 绝密", "没有绝密难度"),
        ("/凑战备 50000", "暂时关闭"),
    ):
        reply = await handle_gear_command(runtime, text, group_id=1, user_id=2)
        assert reply is not None and needle in reply.text, text


async def test_custom_command_when_enabled() -> None:
    plan = LoadoutPlan.from_api(load_data("jzv3_diy.json"))
    service = StubService(plan=plan)
    settings = make_settings(custom_enabled=True)
    reply = await handle_gear_command(StubRuntime(service, settings=settings), "/凑战备 5万 不要枪", group_id=1, user_id=2)

    assert reply is not None and "凑战备 5万" in reply.text
    assert service.calls[0]["kind"] == "custom"
    assert service.calls[0]["target"] == 50000
    assert service.calls[0]["slots"].gun is False


async def test_rate_limit_kicks_in() -> None:
    settings = make_settings(rate_limit_user=1, rate_limit_group=10)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings)

    first = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)
    second = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert first is not None and "频繁" not in first.text
    assert second is not None and "频繁" in second.text


async def test_debug_group_bypasses_rate_limit() -> None:
    settings = make_settings(rate_limit_user=1, rate_limit_group=1)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings, debug_groups={999})

    for _ in range(3):
        reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=999, user_id=2)
        assert reply is not None and "频繁" not in reply.text


async def test_data_source_error_gives_friendly_message() -> None:
    service = StubService(error=DataSourceError("boom"))
    reply = await handle_gear_command(StubRuntime(service), "/卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and "数据源暂时不可用" in reply.text


async def test_quota_exhausted_gives_warning() -> None:
    service = StubService(error=QuotaExhausted("今日数据源资源已用尽"))
    reply = await handle_gear_command(StubRuntime(service), "/卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and reply.text.startswith("⚠️")


async def test_auth_error_mentions_token() -> None:
    service = StubService(error=DataSourceAuthError("bad token"))
    reply = await handle_gear_command(StubRuntime(service), "/卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and "token" in reply.text


async def test_private_chat_is_not_group_limited() -> None:
    settings = make_settings(rate_limit_user=5, rate_limit_group=1)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings)

    for _ in range(3):
        reply = await handle_gear_command(runtime, "/卡战备 巴克什 绝密", group_id=None, user_id=7)
        assert reply is not None and "频繁" not in reply.text
