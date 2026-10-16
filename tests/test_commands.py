"""命令编排测试：从文本到回复文本的完整链路（不联网、不起框架）。"""

from __future__ import annotations

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.models.loadout import LoadoutPlan
from deltaforce_bot.services.commands import handle_gear_command
from deltaforce_bot.services.gear_loadout import GateResult, GearLoadoutService
from tests.support import FakeClient, StubRuntime, StubService, load_data, load_gates, make_settings

GATES = load_gates()


def gate(lv: int):
    return next(item for item in GATES.gates if item.lv == lv)


async def build_result(*, include_exchange: bool = False) -> GateResult:
    service = GearLoadoutService(FakeClient(load_data("jzv4_zb_lv2.json", real=True)), make_settings())
    return await service.by_gate(gate(2), include_exchange=include_exchange, top=3)


async def test_gate_command_end_to_end() -> None:
    service = StubService(gate_result=await build_result())
    reply = await handle_gear_command(StubRuntime(service), "卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert reply is not None
    assert "巴克什 绝密" in reply
    assert "门槛 55万" in reply
    assert service.calls[0]["gate"].lv == 2
    assert service.calls[0]["include_exchange"] is False
    assert service.calls[0]["top"] == 3


async def test_alias_commands() -> None:
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service)

    # 每条命令用不同 user_id，避免撞上用户级限流（限流本身另有测试覆盖）
    assert await handle_gear_command(runtime, "卡战备 集市 绝密", group_id=1, user_id=11) is not None
    assert service.calls[-1]["gate"].lv == 2

    assert await handle_gear_command(runtime, "卡战备 航天基地 绝密", group_id=1, user_id=12) is not None
    assert service.calls[-1]["gate"].lv == 3

    assert await handle_gear_command(runtime, "卡战备 航天 绝密 兑换", group_id=1, user_id=13) is not None
    assert service.calls[-1]["gate"].lv == 3
    assert service.calls[-1]["include_exchange"] is True


async def test_help_and_unsupported_requests() -> None:
    runtime = StubRuntime(StubService())

    assert await handle_gear_command(runtime, "晚上吃什么", group_id=1, user_id=2) is None

    for text, needle in (
        ("卡战备", "请给出地图和难度"),
        ("卡战备 巴克什", "需要指定难度"),
        ("卡战备 巴克什 常规", "常规模式不需要战备值"),
        ("卡战备 大坝 绝密", "没有绝密难度"),
        ("凑战备 50000", "暂时关闭"),
    ):
        reply = await handle_gear_command(runtime, text, group_id=1, user_id=2)
        assert reply is not None and needle in reply, text


async def test_custom_command_when_enabled() -> None:
    plan = LoadoutPlan.from_api(load_data("jzv3_diy.json"))
    service = StubService(plan=plan)
    settings = make_settings(custom_enabled=True)
    reply = await handle_gear_command(StubRuntime(service, settings=settings), "凑战备 5万 不要枪", group_id=1, user_id=2)

    assert reply is not None and "凑战备 5万" in reply
    assert service.calls[0]["kind"] == "custom"
    assert service.calls[0]["target"] == 50000
    assert service.calls[0]["slots"].gun is False


async def test_rate_limit_kicks_in() -> None:
    settings = make_settings(rate_limit_user=1, rate_limit_group=10)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings)

    first = await handle_gear_command(runtime, "卡战备 巴克什 绝密", group_id=1, user_id=2)
    second = await handle_gear_command(runtime, "卡战备 巴克什 绝密", group_id=1, user_id=2)

    assert first is not None and "频繁" not in first
    assert second is not None and "频繁" in second


async def test_debug_group_bypasses_rate_limit() -> None:
    settings = make_settings(rate_limit_user=1, rate_limit_group=1)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings, debug_groups={999})

    for _ in range(3):
        reply = await handle_gear_command(runtime, "卡战备 巴克什 绝密", group_id=999, user_id=2)
        assert reply is not None and "频繁" not in reply


async def test_data_source_error_gives_friendly_message() -> None:
    service = StubService(error=DataSourceError("boom"))
    reply = await handle_gear_command(StubRuntime(service), "卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and "数据源暂时不可用" in reply


async def test_quota_exhausted_gives_warning() -> None:
    service = StubService(error=QuotaExhausted("今日数据源资源已用尽"))
    reply = await handle_gear_command(StubRuntime(service), "卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and reply.startswith("⚠️")


async def test_auth_error_mentions_token() -> None:
    service = StubService(error=DataSourceAuthError("bad token"))
    reply = await handle_gear_command(StubRuntime(service), "卡战备 巴克什 绝密", group_id=1, user_id=2)
    assert reply is not None and "token" in reply


async def test_private_chat_is_not_group_limited() -> None:
    settings = make_settings(rate_limit_user=5, rate_limit_group=1)
    service = StubService(gate_result=await build_result())
    runtime = StubRuntime(service, settings=settings)

    for _ in range(3):
        reply = await handle_gear_command(runtime, "卡战备 巴克什 绝密", group_id=None, user_id=7)
        assert reply is not None and "频繁" not in reply
