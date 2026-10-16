"""卡片数据装配：把接口返回的配装方案整理成模板需要的结构。

对应原型图：
- 左侧四格：头盔 / 胸挂 / 护甲 / 背包（图片 + 名称 + 耐久程度标签 + 等级边框色）
- 右侧：主武器1 / 主武器2 / 副武器，每个武器下方罗列它的配件（文字），无配件则显示"无需配件"
- 配件（type 形如「枪1-瞄具」）不查图，只显示文字
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from deltaforce_bot.models.loadout import LoadoutItem, LoadoutPlan
from deltaforce_bot.render.assets import AssetLibrary, parse_condition
from deltaforce_bot.services.text import format_source_time, humanize_amount

# 等级边框色（用户指定）：1 灰 / 2 绿 / 3 蓝 / 4 紫 / 5 金 / 6 红
LEVEL_COLORS = {
    1: "#9aa5b1",
    2: "#4caf50",
    3: "#2196f3",
    4: "#9c27b0",
    5: "#ffc107",
    6: "#f44336",
}

# 耐久程度底色（用户指定）：全新绿 / 几乎全新黄 / 破损红
CONDITION_COLORS = {"全新": "#2e7d32", "几乎全新": "#f9a825", "破损": "#c62828"}

SLOT_LABELS = {"头盔": "头盔", "胸挂": "胸挂", "护甲": "护甲", "背包": "背包"}


@dataclass
class CardItem:
    """卡片里的一件装备。"""

    label: str  # 部位（如「头盔」「枪1」）
    name: str
    img_url: str | None  # 已 URL 编码的 file:// 地址，供 <img src>
    condition: str | None = None  # 耐久程度（仅护甲/头盔/背包/胸挂）
    condition_color: str | None = None
    border_color: str | None = None  # 等级边框色
    readiness: str = ""  # 战备值（已格式化）
    price: str = ""  # 花费（已格式化）
    bl: int | None = None  # 假账百分比
    exchange: bool = False  # 是否来自军需处兑换


@dataclass
class CardWeapon:
    """卡片里的一个武器槽及其配件。"""

    label: str  # 主武器1 / 主武器2 / 副武器
    name: str
    img_url: str | None
    readiness: str = ""
    price: str = ""
    attachments: list[str] = field(default_factory=list)  # 文字形式的配件列表
    exchange: bool = False  # 枪械本身是否来自军需处兑换


@dataclass
class CardContext:
    map_name: str
    difficulty: str
    difficulty_color: str
    threshold: str
    updated_at: str
    cache_note: str
    background_url: str | None
    slots: list[CardItem | None]
    weapons: list[CardWeapon]
    total_readiness: str
    total_price: str
    saved_text: str
    alternatives: list[str]
    include_exchange: bool

    @property
    def slots_padded(self) -> list[CardItem | None]:
        """补齐到 4 格（头盔/胸挂/护甲/背包 固定顺序）。"""
        return list(self.slots) + [None] * max(0, 4 - len(self.slots))


def _file_url(path: Path | None) -> str | None:
    if path is None:
        return None
    from urllib.parse import quote

    return "file:///" + quote(str(path).replace("\\", "/"))


def _attachment_text(item: LoadoutItem) -> str:
    """配件文字：去掉「枪1-」前缀，显示「瞄具 名称」这样的形式。"""
    sub = (item.type or "").split("-", 1)[-1] if item.type else "配件"
    return f"{sub} {item.name}"


def build_card_context(
    *,
    plan: LoadoutPlan,
    gate_label: str,
    map_name: str,
    difficulty: str,
    threshold: int,
    updated_at: str | None,
    cache_note: str,
    alternatives: list[LoadoutPlan],
    include_exchange: bool,
    assets: AssetLibrary,
) -> CardContext:
    """把一套方案整理成模板上下文。"""
    slots: list[CardItem | None] = []
    weapons: list[CardWeapon] = []
    weapon_map: dict[str, CardWeapon] = {}

    # 固定顺序展示四个装备位（原型图：头盔、胸挂 / 护甲、背包）
    for slot_type in ("头盔", "胸挂", "护甲", "背包"):
        item = next((it for it in plan.items if it.type == slot_type), None)
        if item is None:
            slots.append(None)
            continue
        condition = parse_condition(item.name)
        slots.append(
            CardItem(
                label=SLOT_LABELS[slot_type],
                name=item.name,
                img_url=_file_url(assets.item_image(item.name, item.type, item.grade)),
                condition=condition,
                condition_color=CONDITION_COLORS.get(condition),
                border_color=LEVEL_COLORS.get(item.grade or 0, "#7a8794"),
                readiness=humanize_amount(item.readiness),
                price=humanize_amount(item.price),
                bl=item.bl,
                exchange=bool(item.exchange),
            )
        )

    # 武器与配件：枪1 / 枪2 / 手枪
    for gun_type, label in (("枪1", "主武器1"), ("枪2", "主武器2"), ("手枪", "副武器")):
        gun = next((it for it in plan.items if it.type == gun_type), None)
        attachments = [
            _attachment_text(it)
            for it in plan.items
            if it.type and it.type.startswith(f"{gun_type}-")
        ]
        if gun is None and not attachments:
            continue
        weapon = CardWeapon(
            label=label,
            name=gun.name if gun else "（无）",
            img_url=_file_url(assets.item_image(gun.name, gun.type, gun.grade)) if gun else None,
            readiness=humanize_amount(gun.readiness) if gun else "",
            price=humanize_amount(gun.price) if gun else "",
            attachments=attachments or ["无需配件"],
            exchange=bool(gun.exchange) if gun else False,
        )
        weapons.append(weapon)
        weapon_map[label] = weapon

    saved = plan.saved
    overpaid = plan.overpaid
    saved_text = (
        f"比门槛少花 {humanize_amount(saved)}" if saved is not None
        else (f"比门槛多花 {humanize_amount(overpaid)}" if overpaid is not None else "")
    )

    return CardContext(
        map_name=map_name,
        difficulty=difficulty,
        difficulty_color="#f44336" if difficulty == "绝密" else "#ffffff",
        threshold=humanize_amount(threshold),
        updated_at=format_source_time(updated_at) or "—",
        cache_note=cache_note,
        background_url=_file_url(assets.background(map_name)),
        slots=slots,
        weapons=weapons,
        total_readiness=humanize_amount(plan.jz),
        total_price=humanize_amount(plan.price),
        saved_text=saved_text,
        alternatives=[
            f"{p.group or p.name} {humanize_amount(p.price)}" for p in alternatives[:3]
        ],
        include_exchange=include_exchange,
    )


def card_cache_key(*, gate_label: str, plan: LoadoutPlan, updated_at: str | None, include_exchange: bool) -> str:
    """图片缓存键：档位 + 是否含兑换 + 数据更新时间 + 方案构成。

    数据每 4 小时才更新一次，因此同一档位 4 小时内的重复查询直接复用同一张卡片。
    """
    import hashlib

    signature = "|".join(f"{item.type}:{item.name}:{item.price}" for item in plan.items)
    digest = hashlib.md5(signature.encode("utf-8")).hexdigest()[:12]
    return f"card:{gate_label}:{int(include_exchange)}:{updated_at}:{digest}"
