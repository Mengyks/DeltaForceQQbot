"""每日密码卡片：上下文装配与模板渲染（不启动浏览器）。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.models.daily_password import DailyPasswordEntry, DailyPasswordSnapshot
from deltaforce_bot.render.assets import AssetLibrary
from deltaforce_bot.render.daily_password_card import build_daily_password_context
from deltaforce_bot.render.renderer import TEMPLATE_DIR

TODAY = "2026-10-23"
MAPS = ["零号大坝", "长弓溪谷", "巴克什", "航天基地", "潮汐监狱", "AZ3"]


def make_assets(tmp_path: Path, names: list[str] | None = None) -> AssetLibrary:
    root = tmp_path / "items"
    (root / "backgrounds").mkdir(parents=True)
    for name in names or MAPS:
        (root / "backgrounds" / f"{name}.png").write_bytes(b"x")
    return AssetLibrary(root)


def make_snapshot() -> DailyPasswordSnapshot:
    return DailyPasswordSnapshot(
        day=TODAY,
        source="4399游戏盒",
        updated_at=f"{TODAY}T00:00:00",
        entries=tuple(
            DailyPasswordEntry(
                name=name,
                code=f"{index:04d}",
                location=f"{name}的位置说明",
            )
            for index, name in enumerate(MAPS, start=1)
        ),
    )


def test_build_context_maps_backgrounds_and_codes(tmp_path: Path) -> None:
    context = build_daily_password_context(make_snapshot(), make_assets(tmp_path))

    assert context.day == TODAY
    assert context.source == "4399游戏盒"
    assert context.updated_at == "00:00"
    assert context.push_time == "11:00"
    assert [entry.index for entry in context.entries] == ["01", "02", "03", "04", "05", "06"]
    assert [entry.name for entry in context.entries] == MAPS
    assert context.entries[0].code == "0001"
    assert context.entries[0].location == "零号大坝的位置说明"
    assert all(entry.background_url for entry in context.entries)
    assert context.entries[0].background_url.startswith("file:///")
    assert "%E9%9B%B6" in context.entries[0].background_url  # 「零」被 URL 编码
    assert "az3" in context.entries[-1].background_url.lower()


def test_missing_background_falls_back(tmp_path: Path) -> None:
    assets = make_assets(tmp_path, names=MAPS[:-1])
    context = build_daily_password_context(make_snapshot(), assets)

    assert context.entries[-1].name == "AZ3"
    assert context.entries[-1].background_url is None


def test_template_renders_all_codes(tmp_path: Path) -> None:
    context = build_daily_password_context(make_snapshot(), make_assets(tmp_path))
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("daily_password.html.j2").render(**context.__dict__)

    assert "每日密码" in html
    assert "4399游戏盒" in html
    assert "ACCESS CODE" in html
    for entry in context.entries:
        assert entry.name in html
        assert entry.code in html
    assert "每天" in html and "11:00" in html
    assert "background-image" in html
