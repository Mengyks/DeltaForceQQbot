"""命令解析与消息格式化。

命令设计（按用户要求）：
``/卡战备 <地图> <难度> [兑换]``，唯一可选项是「兑换」（含军需处兑换方案）。
命令必须带斜杠前缀（``/`` 或全角 ``／``），与 ``/每日密码`` 统一。
命令只接受「地图 + 难度」，不接受数字档位写法（按用户要求）。

解析与排版都是纯函数（不碰网络、不碰框架），便于单测覆盖全部分支；
关键词与文案集中在本文件，调整不影响业务逻辑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Literal

from deltaforce_bot.models.gate import Gate, GateConfig, resolve_gate
from deltaforce_bot.models.loadout import LoadoutItem, LoadoutPlan, SlotFlags
from deltaforce_bot.services.gear_loadout import GateResult, MAX_TARGET, MIN_TARGET

TIER_COMMAND = "卡战备"
CUSTOM_COMMAND = "凑战备"
DAILY_PASSWORD_COMMAND = "每日密码"

COMMAND_PATTERN = re.compile(r"^\s*[/／]\s*(?P<cmd>卡战备|凑战备)\s*(?P<args>.*)$", re.S)
DAILY_PASSWORD_COMMAND_PATTERN = re.compile(
    r"^\s*[#＃/／]?\s*(?:每日密码|今日密码)\s*(?:查询)?\s*$"
)

EXCHANGE_KEYWORDS = ("兑换", "军需处", "军需")

# 凑战备（DIY）的部位关键词，功能恢复时直接可用
_RAW_SLOT_KEYWORDS: tuple[tuple[str, str, bool], ...] = (
    ("不要武器", "gun", False),
    ("不带武器", "gun", False),
    ("不要头盔", "helmet", False),
    ("不戴头盔", "helmet", False),
    ("不要护甲", "armor", False),
    ("不要手枪", "pistol", False),
    ("不要双枪", "dual_gun_off", True),
    ("不要胸挂", "chest", False),
    ("不要弹挂", "chest", False),
    ("不要背包", "bag", False),
    ("不要枪", "gun", False),
    ("不带枪", "gun", False),
    ("不穿甲", "armor", False),
    ("不要头", "helmet", False),
    ("不要甲", "armor", False),
    ("不要包", "bag", False),
    ("禁手枪", "pistol", False),
    ("禁双枪", "dual_gun_off", True),
    ("用手枪", "pistol", True),
    ("要手枪", "pistol", True),
    ("带手枪", "pistol", True),
    ("要武器", "gun", True),
    ("要头盔", "helmet", True),
    ("要护甲", "armor", True),
    ("要胸挂", "chest", True),
    ("要背包", "bag", True),
    ("单枪", "dual_gun_off", True),
    ("无枪", "gun", False),
    ("没枪", "gun", False),
    ("无头", "helmet", False),
    ("要枪", "gun", True),
    ("带枪", "gun", True),
    ("要甲", "armor", True),
    ("要包", "bag", True),
)
SLOT_KEYWORDS: tuple[tuple[str, str, bool], ...] = tuple(
    sorted(_RAW_SLOT_KEYWORDS, key=lambda item: len(item[0]), reverse=True)
)

NEGATIONS = ("不要", "不带", "不穿", "不戴", "别", "没", "无", "禁")
SLOT_WORDS: dict[str, str] = {
    "武器": "gun",
    "枪": "gun",
    "头盔": "helmet",
    "头": "helmet",
    "护甲": "armor",
    "甲": "armor",
    "胸挂": "chest",
    "弹挂": "chest",
    "背包": "bag",
    "包": "bag",
    "手枪": "pistol",
    "双枪": "dual_gun_off",
}

_FULLWIDTH = str.maketrans("０１２３４５６７８９ｗＷ", "0123456789ww")
_AMOUNT_PATTERN = re.compile(r"^(?P<num>\d+(?:\.\d+)?)(?P<unit>[w万])?$")

CUSTOM_DISABLED_NOTE = (
    "凑战备（自定义目标战备值）暂时关闭：该接口每次请求消耗 10 次数据源资源，成本较高。\n"
    "当前可用：/卡战备 <地图> <难度> [兑换]，例如「/卡战备 巴克什 绝密」。"
)


@dataclass(frozen=True)
class Query:
    kind: Literal["help", "gate", "custom"]
    gate: Gate | None = None
    include_exchange: bool = False
    target: int | None = None
    slots: SlotFlags = field(default_factory=SlotFlags)
    error: str | None = None


def normalize(text: str) -> str:
    return (text or "").translate(_FULLWIDTH).strip()


def parse_amount(token: str) -> int | None:
    """解析金额：支持 50000 / 5万 / 5w / 5.5万。"""
    cleaned = normalize(token).replace(",", "").replace("，", "")
    match = _AMOUNT_PATTERN.match(cleaned)
    if not match:
        return None
    value = float(match.group("num"))
    if match.group("unit"):
        value *= 10000
    return int(value)


def apply_slot_keywords(tokens: Iterable[str], base: SlotFlags | None = None) -> tuple[SlotFlags, list[str]]:
    """把部位关键词应用到开关上，返回（开关, 无法识别的词）。支持连写与分词两种写法。"""
    slots = base or SlotFlags()
    unknown: list[str] = []
    pending_negation = False

    for raw_token in tokens:
        token = normalize(raw_token)
        if not token:
            continue

        for keyword, slot, enabled in SLOT_KEYWORDS:
            if keyword in token:
                slots = slots.with_slot(slot, enabled)
                token = token.replace(keyword, " ")
        token = token.strip()
        if not token:
            continue

        if token in NEGATIONS:
            pending_negation = True
            continue
        if token in SLOT_WORDS:
            slot = SLOT_WORDS[token]
            if slot == "dual_gun_off":
                slots = slots.with_slot("dual_gun_off", pending_negation)
            else:
                slots = slots.with_slot(slot, not pending_negation)
            pending_negation = False
            continue

        pending_negation = False
        unknown.append(token)

    if pending_negation:
        unknown.append("不要")

    return slots, unknown


def parse(text: str, config: GateConfig, *, custom_enabled: bool = False) -> Query | None:
    """解析消息文本。不是本插件的命令时返回 None。"""
    match = COMMAND_PATTERN.match(normalize(text))
    if not match:
        return None

    command = match.group("cmd")
    tokens = (match.group("args") or "").split()

    include_exchange = False
    cleaned: list[str] = []
    for token in tokens:
        if token in EXCHANGE_KEYWORDS:
            include_exchange = True
            continue
        cleaned.append(token)

    if command == CUSTOM_COMMAND:
        if not custom_enabled:
            return Query(kind="help", error=CUSTOM_DISABLED_NOTE)
        return _parse_custom(cleaned, include_exchange)

    # 卡战备：地图 + 难度 [+ 兑换]
    if not cleaned:
        return Query(kind="help", error="请给出地图和难度，例如：/卡战备 巴克什 绝密")
    map_text = cleaned[0]
    difficulty_text = cleaned[1] if len(cleaned) > 1 else None
    if len(cleaned) > 2:
        return Query(kind="help", error=f"参数多了：{' '.join(cleaned[2:])}（格式：/卡战备 <地图> <难度> [兑换]）")

    resolution = resolve_gate(config, map_text, difficulty_text)
    if not resolution.ok:
        return Query(kind="help", error=resolution.error)
    return Query(kind="gate", gate=resolution.gate, include_exchange=include_exchange)


def _parse_custom(tokens: list[str], include_exchange: bool) -> Query:
    if not tokens:
        return Query(kind="help", error=f"请给出目标战备值，例如：/{CUSTOM_COMMAND} 5万")
    target = parse_amount(tokens[0])
    if target is None:
        return Query(
            kind="help",
            error=f"无法识别目标战备值「{tokens[0]}」，请写成数字，例如：/{CUSTOM_COMMAND} 50000 或 /{CUSTOM_COMMAND} 5万",
        )
    if not (MIN_TARGET <= target <= MAX_TARGET):
        return Query(
            kind="help",
            error=f"目标战备值需要在 {MIN_TARGET}~{MAX_TARGET} 之间（当前：{target}）",
        )

    slots, unknown = apply_slot_keywords(tokens[1:])
    if unknown:
        return Query(kind="help", error=f"无法识别的条件：{'、'.join(unknown)}")
    return Query(kind="custom", target=target, include_exchange=include_exchange, slots=slots)


def humanize_amount(value: int) -> str:
    """金额/战备值统一按「万」缩写：113200 → 11.32万，780000 → 78万。"""
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount < 10000:
        return f"{sign}{amount}"
    text = f"{amount / 10000:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{text}万"


def humanize_duration(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def format_cache_note(cache_age: float | None) -> str:
    """缓存新鲜度提示（超过 90 秒才提示，避免污染正常回复）。"""
    if cache_age is None or cache_age < 90:
        return ""
    return f"（{humanize_duration(cache_age)}前的缓存）"


def format_source_time(text: str | None) -> str | None:
    """数据源的时间戳 '2026-10-15 18:30:39' → 当天只显示 '18:30'，跨天显示 '10-15 18:30'。"""
    if not text:
        return None
    try:
        date_part, time_part = text.strip().split(" ", 1)
        hour_minute = ":".join(time_part.split(":")[:2])
        if date_part == datetime.now().strftime("%Y-%m-%d"):
            return hour_minute
        month, day = date_part.split("-")[1:3]
        return f"{month}-{day} {hour_minute}"
    except (ValueError, IndexError):
        return text


def _item_line(item: LoadoutItem) -> str:
    parts = [f"· {item.type + ' ' if item.type else ''}{item.name}"]
    parts.append(f"战备{humanize_amount(item.readiness)}")
    parts.append(f"花{humanize_amount(item.price)}")
    if item.exchange:
        parts.append("〔兑换〕")
    if item.bl:
        parts.append(f"假账{item.bl:+d}%")
    return "  ".join(parts)


def _plan_lines(plan: LoadoutPlan, title: str) -> list[str]:
    lines = [f"{title}{'〔含兑换〕' if plan.is_exchange else ''}"]
    summary = f"   总战备 {humanize_amount(plan.jz)} ｜ 花费 {humanize_amount(plan.price)}"
    if plan.saved is not None:
        summary += f" ｜ 比门槛少花 {humanize_amount(plan.saved)}"
    elif plan.overpaid is not None:
        summary += f" ｜ 比门槛多花 {humanize_amount(plan.overpaid)}"
    lines.append(summary)
    lines.extend("   " + _item_line(item) for item in plan.items)
    return lines


def _compact_plan_line(plan: LoadoutPlan) -> str:
    text = f"· {plan.group or plan.name}：花费 {humanize_amount(plan.price)}"
    if plan.saved is not None:
        text += f"（少花 {humanize_amount(plan.saved)}）"
    if plan.is_exchange:
        text += "〔含兑换〕"
    return text


def format_help(config: GateConfig, *, custom_enabled: bool = False) -> str:
    lines = [
        "【卡战备】用最低成本把战备值卡到地图门槛。",
        "",
        "用法：/卡战备 <地图> <难度> [兑换]",
        "· /卡战备 巴克什 绝密",
        "· /卡战备 航天 绝密 兑换   （含军需处兑换方案）",
        "",
        "可查档位：",
    ]
    for gate in config.gates:
        note = f"，{gate.note}" if gate.note else ""
        lines.append(f"· {gate.label} → 门槛 {humanize_amount(gate.threshold)}{note}")
    lines += [
        "",
        f"{config.regular_note}。",
        "提示：方案里的「假账 -16%」表示该件战备值比价格高约 16%（越负越划算）。",
        "数据每 4 小时自动更新一次，期间同样的查询直接复用缓存。",
    ]
    if custom_enabled:
        lines.append(f"另有 /{CUSTOM_COMMAND} <目标战备值> 可自定义凑战备。")
    return "\n".join(lines)


def format_gate_result(result: GateResult, *, top: int = 3) -> str:
    gate = result.gate
    header = f"【卡战备 · {gate.label}】门槛 {humanize_amount(result.threshold)}"
    source_time = format_source_time(result.updated_at)
    if source_time:
        header += f" ｜ 数据源 {source_time}"
    header += format_cache_note(result.cache_age)
    lines = [header]

    if result.best is None:
        lines.append("这个档位暂时没取到方案，稍后再试或换个难度。")
        return "\n".join(lines)

    lines.append("")
    lines.extend(_plan_lines(result.best, f"✅ 最省 · {result.best.group or result.best.name}"))

    if result.alternatives:
        lines += ["", "其他配装思路（各取最省一套）："]
        lines.extend(_compact_plan_line(plan) for plan in result.alternatives[:top])

    tips = [f"本档位共 {result.total} 套候选"]
    if not result.include_exchange:
        tips.append("想包含军需处兑换加「兑换」")
    lines += ["", "提示：" + "；".join(tips) + "。"]
    return "\n".join(lines)


def format_custom_plan(plan: LoadoutPlan, target: int) -> str:
    lines = [f"【凑战备 {humanize_amount(target)}】", ""]
    lines.extend(_plan_lines(plan, "✅ 方案"))
    lines += ["", "提示：想放宽条件可以去掉「不要…」；目标值越高可选装备越多。"]
    return "\n".join(lines)
