"""渲染相关测试：资源映射、卡片数据装配、缓存键。

不需要浏览器（渲染器本身在预览脚本与真机验证）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltaforce_bot.render.assets import (
    AssetLibrary,
    is_attachment,
    normalize_name,
    parse_condition,
)
from deltaforce_bot.render.card import (
    CONDITION_COLORS,
    LEVEL_COLORS,
    build_card_context,
    card_cache_key,
)
from deltaforce_bot.services.gear_loadout import parse_groups
from tests.support import load_data


def make_assets(tmp_path: Path) -> AssetLibrary:
    """构造一个最小资源目录：护甲 2 个等级、头盔 1 个、枪 1 把、背景 1 张。"""
    root = tmp_path / "items"
    (root / "armor" / "Level3").mkdir(parents=True)
    (root / "armor" / "Level5").mkdir(parents=True)
    (root / "helmets" / "Level3").mkdir(parents=True)
    (root / "weapon").mkdir(parents=True)
    (root / "backgrounds").mkdir(parents=True)

    (root / "armor" / "Level3" / "尼龙防弹衣.png").write_bytes(b"x")
    (root / "armor" / "Level5" / "金刚防弹衣.png").write_bytes(b"x")
    (root / "helmets" / "Level3" / "DAS防弹头盔.png").write_bytes(b"x")  # 注意：无空格
    (root / "weapon" / "CAR-15突击步枪.png").write_bytes(b"x")
    (root / "backgrounds" / "巴克什.png").write_bytes(b"x")
    return AssetLibrary(root)


def test_normalize_name_handles_condition_spaces_and_case() -> None:
    assert normalize_name("DAS 防弹头盔 (几乎全新)") == "das防弹头盔"
    assert normalize_name("DAS防弹头盔") == "das防弹头盔"
    assert normalize_name("H09 防暴头盔（破损）") == "h09防暴头盔"
    assert normalize_name("3/7可调倍率狙击镜") == "3/7可调倍率狙击镜"


def test_parse_condition_defaults_to_new() -> None:
    assert parse_condition("DAS 防弹头盔 (几乎全新)") == "几乎全新"
    assert parse_condition("防暴头盔 (破损)") == "破损"
    assert parse_condition("H70 精英头盔") == "全新"


def test_is_attachment() -> None:
    assert is_attachment("枪1-瞄具") is True
    assert is_attachment("枪2-枪管") is True
    assert is_attachment("枪1") is False
    assert is_attachment("头盔") is False
    assert is_attachment(None) is False


def test_item_image_maps_by_slot_and_level(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)

    # 护甲按等级找目录；名称带空格/耐久度后缀也能命中
    assert assets.item_image("尼龙防弹衣 (破损)", "护甲", 3).name == "尼龙防弹衣.png"
    assert assets.item_image("金刚防弹衣", "护甲", 5).name == "金刚防弹衣.png"
    # 等级目录里没有就同部位兜底
    assert assets.item_image("尼龙防弹衣", "护甲", 4).name == "尼龙防弹衣.png"
    # 头盔：API 名有空格，文件名没有
    assert assets.item_image("DAS 防弹头盔 (几乎全新)", "头盔", 3).name == "DAS防弹头盔.png"
    # 枪械走 weapon 目录
    assert assets.item_image("CAR-15突击步枪", "枪1", 0).name == "CAR-15突击步枪.png"
    # 配件不查图
    assert assets.item_image("3/7可调倍率狙击镜", "枪1-瞄具", 4) is None
    # 找不到的返回 None（模板渲染占位块）
    assert assets.item_image("不存在的护甲", "护甲", 2) is None


def test_background_lookup(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    assert assets.background("巴克什").name == "巴克什.png"
    assert assets.background("不存在的地图") is None


def load_best_plan():
    groups = parse_groups(load_data("jzv4_zb_lv2.json", real=True))
    return groups[0].plans[0], groups


def test_build_card_context_groups_items(tmp_path: Path) -> None:
    plan, _ = load_best_plan()
    assets = make_assets(tmp_path)

    context = build_card_context(
        plan=plan,
        gate_label="巴克什 绝密",
        map_name="巴克什",
        difficulty="绝密",
        threshold=550000,
        updated_at="2026-10-15 18:30:23",
        cache_note="",
        alternatives=[],
        include_exchange=False,
        assets=assets,
    )

    # 标题与难度配色（绝密=红，机密=白）
    assert context.map_name == "巴克什" and context.difficulty == "绝密"
    assert context.difficulty_color == "#f44336"
    # 时间戳来自录制的历史数据（不是今天），因此会带上日期；同一天只显示时分
    assert context.updated_at.endswith("18:30")

    # 四个装备位按固定顺序排列（头盔、胸挂、护甲、背包），方案里没有的位置为 None
    assert len(context.slots_padded) == 4
    slots = context.slots_padded
    assert slots[0] is not None and slots[0].label == "头盔"   # 该方案有头盔
    assert slots[1] is None and slots[3] is None               # 该方案没有胸挂/背包
    assert slots[2] is not None and slots[2].label == "护甲"

    # 武器分组：同一把枪的配件归到它下面；没有配件的武器显示"无需配件"
    assert context.weapons, "至少应有一套武器"
    for weapon in context.weapons:
        assert weapon.attachments, "配件列表不能为空（至少是 无需配件）"

    assert context.total_readiness and context.total_price
    assert "比门槛" in context.saved_text


def test_build_card_context_marks_missing_attachments(tmp_path: Path) -> None:
    """没有配件的武器应显示「无需配件」（原型要求）。"""
    from deltaforce_bot.models.loadout import LoadoutItem, LoadoutPlan

    plan = LoadoutPlan(
        name="测试方案",
        jz=112500,
        price=90000,
        cz=-22500,
        items=[
            LoadoutItem(name="CAR-15突击步枪", type="枪1", price=7678, jz=-4100, grade=0),
            LoadoutItem(name="安保防弹衣", type="护甲", price=2390, jz=111, grade=1),
        ],
    )
    context = build_card_context(
        plan=plan,
        gate_label="az3 机密",
        map_name="az3",
        difficulty="机密",
        threshold=112500,
        updated_at=None,
        cache_note="",
        alternatives=[],
        include_exchange=False,
        assets=make_assets(tmp_path),
    )

    assert context.difficulty_color == "#ffffff"  # 机密白字
    assert context.weapons[0].attachments == ["无需配件"]
    assert context.weapons[0].img_url is not None  # 枪图能查到


def test_level_and_condition_colors_are_complete() -> None:
    for level, color in LEVEL_COLORS.items():
        assert color.startswith("#")
    assert set(LEVEL_COLORS) == {1, 2, 3, 4, 5, 6}
    assert CONDITION_COLORS["全新"].startswith("#")
    assert CONDITION_COLORS["破损"].startswith("#")


def test_card_cache_key_changes_with_plan_and_data() -> None:
    plan, _ = load_best_plan()
    base = dict(gate_label="巴克什 绝密", plan=plan, updated_at="2026-10-15 18:30:23", include_exchange=False)

    key = card_cache_key(**base)
    assert key == card_cache_key(**base)  # 稳定
    assert key != card_cache_key(**{**base, "include_exchange": True})
    assert key != card_cache_key(**{**base, "updated_at": "2026-10-15 19:30:00"})
    assert key != card_cache_key(**{**base, "gate_label": "航天基地 绝密"})


def test_format_source_time_today_shows_time_only() -> None:
    from datetime import datetime

    from deltaforce_bot.services.text import format_source_time

    today = datetime.now().strftime("%Y-%m-%d")
    assert format_source_time(f"{today} 14:05:33") == "14:05"
    assert format_source_time("2020-01-02 03:04:05") == "01-02 03:04"
    assert format_source_time(None) is None
