"""连接看护（掉线监控）的单元测试：状态、文案、告警载荷映射。"""

from __future__ import annotations

from deltaforce_bot.watchdog import (
    ConnectionWatchdog,
    build_webhook_payload,
    format_recovery_message,
    parse_alert_users,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_online_offline_state_machine() -> None:
    clock = FakeClock()
    watchdog = ConnectionWatchdog(time_fn=clock)

    watchdog.mark_online()
    clock.now += 3600
    watchdog.mark_offline()
    clock.now += 3 * 3600 + 600

    downtime, reason = watchdog.take_offline_summary()

    assert downtime == 3 * 3600 + 600
    assert reason is None
    assert watchdog.state.offline_count == 1


def test_reason_is_consumed_once() -> None:
    clock = FakeClock()
    watchdog = ConnectionWatchdog(time_fn=clock)
    watchdog.mark_online()
    watchdog.mark_offline()
    watchdog.record_reason("你的账号当前登录已失效，请重新登录。")
    clock.now += 120

    downtime, reason = watchdog.take_offline_summary()
    assert "登录已失效" in reason
    assert downtime == 120

    # 第二次取不应再带上旧原因
    _, reason_again = watchdog.take_offline_summary()
    assert reason_again is None


def test_offline_count_accumulates() -> None:
    watchdog = ConnectionWatchdog()
    for _ in range(3):
        watchdog.mark_offline()
        watchdog.mark_online()
    assert watchdog.state.offline_count == 3


def test_recovery_message_contains_key_facts() -> None:
    text = format_recovery_message(downtime=5 * 3600, reason="你的账号当前登录已失效，请重新登录。", count=2)

    assert "离线了 5.0 小时" in text
    assert "登录已失效" in text
    assert "累计掉线 2 次" in text
    assert "重新扫码" in text


def test_recovery_message_without_reason() -> None:
    text = format_recovery_message(downtime=90, reason=None, count=1)
    assert "1 分钟" in text
    assert "原因（QQ 侧）" not in text  # 没有原因时不带这一行
    assert "累计掉线" not in text


def test_webhook_payload_formats() -> None:
    assert build_webhook_payload("serverchan", "标题", "内容") == {"title": "标题", "desp": "内容"}
    assert build_webhook_payload("feishu", "标题", "内容")["msg_type"] == "text"
    assert build_webhook_payload("dingtalk", "标题", "内容")["msgtype"] == "text"
    assert build_webhook_payload("json", "标题", "内容") == {"title": "标题", "text": "内容"}
    assert build_webhook_payload("未知格式", "标题", "内容") == {"title": "标题", "text": "内容"}


def test_parse_alert_users_tolerates_separators() -> None:
    assert parse_alert_users("1826124962, 3877496716") == [1826124962, 3877496716]
    assert parse_alert_users("1826124962，3877496716") == [1826124962, 3877496716]
    assert parse_alert_users("") == []
    assert parse_alert_users("abc, 123") == [123]


def test_notice_annotation_is_resolvable_in_module_globals() -> None:
    """回归测试：handler 的注解必须能在模块全局命名空间解析。

    实际部署时踩到过：`from __future__ import annotations` 让注解变成字符串，
    NoneBot 注册 handler 时按模块全局命名空间求值；若 Bot/NoticeEvent 只在函数内导入，
    就会 `NameError: name 'NoticeEvent' is not defined`，导致 bot_offline 处理器静默失效。
    """
    import deltaforce_bot.watchdog as watchdog_module

    assert "NoticeEvent" in vars(watchdog_module)
    assert "Bot" in vars(watchdog_module)


async def test_install_watchdog_registers_handlers() -> None:
    """集成测试：真的调用一次 install_watchdog，确保注册过程不抛异常。"""
    from deltaforce_bot.watchdog import ConnectionWatchdog, install_watchdog
    from tests.support import make_settings

    watchdog = install_watchdog(make_settings(alert_qq="123456, 789"))

    assert isinstance(watchdog, ConnectionWatchdog)
    assert watchdog.state.offline_count == 0
