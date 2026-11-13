"""帮助页的静态内容与纯文本兜底。

内容集中在这里，卡片和文字兜底共用同一份指令说明；
新增功能时只改这个文件即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field

HELP_TITLE = "五点老师 · 帮助命令"
HELP_SUBTITLE = "输入斜杠命令，或直接 @五点 聊天"
HELP_FOOTER = "其他模块的指令类似 ｜ 发送 /帮助 随时查看 ｜ 数据仅供参考"


@dataclass(frozen=True)
class HelpCommandSpec:
    command: str
    example: str = ""


@dataclass(frozen=True)
class HelpSection:
    title: str
    badge: str
    accent: str
    commands: tuple[HelpCommandSpec, ...] = field(default_factory=tuple)
    note: str = ""


HELP_SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        title="卡战备",
        badge="数据",
        accent="#e53935",
        commands=(
            HelpCommandSpec(
                "/卡战备 <地图> <难度> [兑换]",
                "/卡战备 巴克什 绝密",
            ),
        ),
        note="「兑换」可选，包含军需处兑换方案",
    ),
    HelpSection(
        title="每日密码",
        badge="每日",
        accent="#d4a72c",
        commands=(
            HelpCommandSpec("/每日密码", "/每日密码"),
        ),
        note="每天 11:00 自动推送，群友可随时查询",
    ),
    HelpSection(
        title="制造利润",
        badge="制造",
        accent="#12805c",
        commands=(
            HelpCommandSpec("/制造利润", "/制造利润"),
        ),
        note="四个制造台 Lv3；结果缓存 12 小时",
    ),
    HelpSection(
        title="直播订阅",
        badge="B站",
        accent="#fb7299",
        commands=(
            HelpCommandSpec("/直播订阅 添加 <B站UID>", "/直播订阅 添加 672328094"),
            HelpCommandSpec("/直播订阅 列表"),
            HelpCommandSpec("/直播订阅 查询 <B站UID>"),
            HelpCommandSpec("/直播订阅 删除 <B站UID>"),
        ),
        note="仅群主/群管理员/SUPERUSERS 可增删；订阅只在本群生效；仅支持 B 站",
    ),
    HelpSection(
        title="群聊",
        badge="聊天",
        accent="#7c6cf0",
        commands=(
            HelpCommandSpec("@五点 / 引用机器人消息", "@五点 今天绝航亏麻了"),
        ),
        note="群里直接聊天即可，回复会拆成短句",
    ),
    HelpSection(
        title="管理员",
        badge="权限",
        accent="#6b7280",
        commands=(
            HelpCommandSpec("@五点 上下文清理 提取摘要"),
            HelpCommandSpec("@五点 上下文清理 直接清理"),
        ),
        note="仅 SUPERUSERS 可用",
    ),
    HelpSection(
        title="猪猪表情包",
        badge="娱乐",
        accent="#d977a8",
        commands=(
            HelpCommandSpec("发「猪 / zhu / pig / 🐖 / 🐷」", "猪"),
        ),
        note="精确触发，随机回一张本地猪图",
    ),
    HelpSection(
        title="猪结婚",
        badge="娱乐",
        accent="#e07a5f",
        commands=(
            HelpCommandSpec("/waifu", "/waifu"),
        ),
        note="每天每群配对一次，重复发送结果不变",
    ),
    HelpSection(
        title="每日天尊挑战",
        badge="娱乐",
        accent="#2f8f9d",
        commands=(
            HelpCommandSpec("/每日天尊挑战", "/每日天尊挑战"),
        ),
        note="随机怪物 + 武器，同一人当天结果不变",
    ),
)


def format_help_text() -> str:
    """渲染失败时的文字版帮助，和卡片内容保持一致。"""
    lines = [f"【{HELP_TITLE}】", HELP_SUBTITLE, ""]
    for section in HELP_SECTIONS:
        lines.append(f"【{section.title}】")
        for command in section.commands:
            lines.append(f"  {command.command}")
            if command.example:
                lines.append(f"    示例：{command.example}")
        if section.note:
            lines.append(f"    提示：{section.note}")
        lines.append("")
    lines.append(HELP_FOOTER)
    return "\n".join(lines)
