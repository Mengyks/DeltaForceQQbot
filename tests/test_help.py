"""帮助页静态内容测试。"""

from __future__ import annotations

from deltaforce_bot.services.help import HELP_SECTIONS, format_help_text


def test_sections_cover_all_features() -> None:
    titles = [section.title for section in HELP_SECTIONS]
    assert titles == [
        "卡战备",
        "每日密码",
        "制造利润",
        "直播订阅",
        "群聊",
        "管理员",
        "猪猪表情包",
        "猪结婚",
        "每日天尊挑战",
    ]

    commands = [
        command.command
        for section in HELP_SECTIONS
        for command in section.commands
    ]
    assert any("/卡战备" in command for command in commands)
    assert any("/每日密码" in command for command in commands)
    assert any("/制造利润" in command for command in commands)
    assert any("/直播订阅 添加" in command for command in commands)
    assert any("/waifu" in command for command in commands)
    assert any("猪 / zhu / pig" in command for command in commands)
    assert any("/每日天尊挑战" in command for command in commands)
    assert any("@五点" in command for command in commands)
    assert any("上下文清理" in command for command in commands)


def test_every_section_has_example_or_note() -> None:
    for section in HELP_SECTIONS:
        assert section.commands
        assert any(command.example for command in section.commands) or section.note


def test_text_fallback_contains_commands_and_examples() -> None:
    text = format_help_text()
    assert "【五点老师 · 帮助命令】" in text
    assert "/卡战备 <地图> <难度> [兑换]" in text
    assert "示例：/卡战备 巴克什 绝密" in text
    assert "/每日密码" in text
    assert "/制造利润" in text
    assert "/直播订阅 添加 <B站UID>" in text
    assert "/waifu" in text
    assert "/每日天尊挑战" in text
    assert "@五点 今天绝航亏麻了" in text
    assert "仅 SUPERUSERS" in text
    assert "发送 /帮助" in text
