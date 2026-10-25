"""制造台图片索引测试：台/等级映射与宽松名称匹配。"""

from __future__ import annotations

from pathlib import Path

from deltaforce_bot.models.crafting import CraftingItem
from deltaforce_bot.render.crafting_assets import CraftingAssetLibrary, canonical_name


def make_item(
    name: str,
    *,
    grade: int = 4,
    primary_class: str = "",
    second_class: str = "",
) -> CraftingItem:
    return CraftingItem(
        name=name,
        grade=grade,
        period=4.0,
        price=1000,
        price_hour=250,
        primary_class=primary_class,
        second_class=second_class,
    )


def touch(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-png")
    return path


def make_assets(tmp_path: Path) -> CraftingAssetLibrary:
    root = tmp_path / "items"
    touch(root, "accessories/level4/CR棱镜阻手器.png")
    touch(root, "accessories/level4/3_7可调倍率狙击镜.png")
    touch(root, "weapon/MCX-LT突击步枪.png")
    touch(root, "weapon/PERST-7蓝色激光镭指.png")
    touch(root, "bullet/level3/300BLK_3.png")
    touch(root, "bullet/level4/357 Magnum FMJ.png")
    touch(root, "bullet/level3/357 Magnum 9号霰射鼠弹.png")
    touch(root, "supplies/level5/战地医疗箱.png")
    touch(root, "helmets/Level5/重型头盔.png")
    touch(root, "backpacks/Level4/MAP侦察背包.png")
    touch(root, "armor/Level5/重型突击背心.png")
    touch(root, "chest_rigs/Level4/强袭战术背心.png")
    return CraftingAssetLibrary(root)


def test_canonical_name_handles_punctuation() -> None:
    assert canonical_name(".300BLK_3") == canonical_name("300BLK_3")
    assert canonical_name("MCX LT突击步枪") == canonical_name("MCX-LT突击步枪")
    assert canonical_name('357 Magnum 9号霰射"鼠弹"') == canonical_name(
        "357 Magnum 9号霰射鼠弹"
    )
    assert canonical_name("3/7可调倍率狙击镜") == canonical_name("3_7可调倍率狙击镜")


def test_tech_center_accessory_and_weapon(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)

    acc = assets.find(1, make_item("CR棱镜阻手器", grade=4, primary_class="acc"))
    assert acc is not None and acc.name == "CR棱镜阻手器.png"

    gun = assets.find(
        1,
        make_item("MCX LT突击步枪", grade=1, primary_class="gun"),
    )
    assert gun is not None and gun.name == "MCX-LT突击步枪.png"

    weapon = assets.find(
        1,
        make_item("PERST-7蓝色激光镭指", grade=4, primary_class="gun"),
    )
    assert weapon is not None and weapon.name == "PERST-7蓝色激光镭指.png"


def test_tech_center_slash_and_underscore(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    hit = assets.find(
        1,
        make_item("3/7可调倍率狙击镜", grade=4, primary_class="acc"),
    )
    assert hit is not None and hit.name == "3_7可调倍率狙击镜.png"


def test_workbench_bullet_names(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    hit = assets.find(2, make_item(".300BLK_3", grade=3, primary_class="ammo"))
    assert hit is not None and hit.name == "300BLK_3.png"

    hit = assets.find(
        2,
        make_item('357 Magnum 9号霰射"鼠弹"', grade=3, primary_class="ammo"),
    )
    assert hit is not None and hit.name == "357 Magnum 9号霰射鼠弹.png"


def test_pharma_supplies(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    hit = assets.find(3, make_item("战地医疗箱", grade=5, primary_class="props"))
    assert hit is not None and hit.parent.name == "level5"


def test_armor_slots(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    cases = [
        ("重型头盔", "helmet", "helmets", "Level5"),
        ("MAP侦察背包", "bag", "backpacks", "Level4"),
        ("重型突击背心", "armor", "armor", "Level5"),
        ("强袭战术背心", "chest", "chest_rigs", "Level4"),
    ]
    for name, second, folder, level in cases:
        hit = assets.find(4, make_item(name, grade=5 if level == "Level5" else 4, second_class=second))
        assert hit is not None
        assert hit.parent.name == level
        assert hit.parent.parent.name == folder


def test_wrong_level_falls_back_and_missing_returns_none(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    # 图放在 level3，但 API grade=4：仍然给图，避免整格空白
    hit = assets.find(2, make_item("300BLK_3", grade=4, primary_class="ammo"))
    assert hit is not None and hit.parent.name == "level3"

    assert assets.find(2, make_item("完全不存在的子弹", grade=3)) is None
    assert assets.find(1, make_item("不存在的配件", grade=4, primary_class="acc")) is None


def test_stats(tmp_path: Path) -> None:
    assets = make_assets(tmp_path)
    stats = assets.stats
    assert stats["supplies"] == 1
    assert stats["accessories"] == 2
    assert stats["bullet"] == 3
    assert stats["weapon"] == 2
