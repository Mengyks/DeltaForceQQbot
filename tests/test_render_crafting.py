"""制造台利润卡片：上下文、模板字段与压缩阶梯。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.models.crafting import (
    STATIONS,
    CraftingItem,
    CraftingResult,
    CraftingStationResult,
)
from deltaforce_bot.render.card import LEVEL_COLORS
from deltaforce_bot.render.crafting_card import (
    QUALITY_LADDER,
    build_crafting_card_context,
    render_crafting_card,
)
from deltaforce_bot.render.renderer import TEMPLATE_DIR, RenderError


def make_item(name: str, grade: int, price: int, hourly: int) -> CraftingItem:
    return CraftingItem(
        name=name,
        grade=grade,
        period=4.0,
        price=price,
        price_hour=hourly,
    )


def make_result() -> CraftingResult:
    items = [
        make_item("PERST-7蓝色激光镭指", 4, 35752, 7944),
        make_item(".300BLK_5", 5, 326449, 40806),
        make_item("高级头盔维修组合", 6, 82464, 10308),
        make_item("重型突击背心", 5, 241544, 30193),
    ]
    return CraftingResult(
        tuple(
            CraftingStationResult(station_id, label, item)
            for (station_id, label), item in zip(STATIONS, items, strict=True)
        ),
        fetched_at=datetime(2026, 10, 25, 10, 15).timestamp(),
    )


class StubAssets:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def find(self, station_id: int, item: CraftingItem):
        return self.path if station_id == 1 else None


def test_build_context_uses_border_colors_and_profits(tmp_path: Path) -> None:
    cover = tmp_path / "item.png"
    cover.write_bytes(b"fake")
    context = build_crafting_card_context(make_result(), StubAssets(cover))

    assert context.title == "每日利润"
    assert context.subtitle == "特勤处制造 · Lv3"
    assert context.query_time == "2026-10-25 10:15"
    assert "数据帝 Pro" in context.source_note
    assert "12 小时" in context.source_note
    assert [item.station for item in context.items] == [
        "技术中心",
        "工作台",
        "制药台",
        "防具台",
    ]
    assert context.items[0].border_color == LEVEL_COLORS[4]
    assert context.items[1].border_color == LEVEL_COLORS[5]
    assert context.items[2].border_color == LEVEL_COLORS[6]
    assert context.items[0].profit_text == "+35,752"
    assert context.items[0].hourly_text == "+7,944"
    assert context.items[0].image_url is not None
    assert context.items[0].image_url.startswith("file:///")
    assert context.items[1].image_url is None


def test_error_station_context(tmp_path: Path) -> None:
    result = CraftingResult(
        (
            CraftingStationResult(1, "技术中心", None, "down"),
            CraftingStationResult(2, "工作台", make_item("x", 4, 1, 1)),
        )
    )
    context = build_crafting_card_context(result, StubAssets())
    assert context.items[0].error is True
    assert context.items[0].item_name == "数据获取失败"
    assert context.items[0].profit_text == "—"
    assert context.items[1].error is False


def test_template_contains_layout_fields(tmp_path: Path) -> None:
    context = build_crafting_card_context(make_result(), StubAssets(tmp_path / "item.png"))
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("crafting_profit.html.j2").render(**context.__dict__)

    assert "每日利润" in html
    assert "特勤处制造 · Lv3" in html
    assert "查询时间：2026-10-25 10:15" in html
    for station in ("技术中心", "工作台", "制药台", "防具台"):
        assert station in html
    assert "PERST-7蓝色激光镭指" in html
    assert "+35,752" in html
    assert "+7,944" in html
    assert "单次利润" in html and "每小时利润" in html
    assert LEVEL_COLORS[4] in html
    assert "数据帝 Pro" in html


class StubRenderer:
    def __init__(self, sizes: dict[int, int], *, error: Exception | None = None) -> None:
        self.sizes = sizes
        self.error = error
        self.calls: list[dict] = []

    async def render_template(self, context, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        quality = int(kwargs.get("quality") or 0)
        return b"x" * self.sizes.get(quality, 0)


async def test_render_quality_ladder(tmp_path: Path) -> None:
    context = build_crafting_card_context(make_result(), StubAssets())
    renderer = StubRenderer({86: 900, 78: 500, 70: 200})

    image = await render_crafting_card(renderer, context, target_bytes=300, hard_limit_bytes=1000)

    assert image is not None and len(image) == 200
    assert [call["quality"] for call in renderer.calls] == [86, 78, 70]
    assert all(call["viewport_width"] == 920 for call in renderer.calls)
    assert all(call["screenshot_type"] == "jpeg" for call in renderer.calls)


async def test_render_large_or_failed(tmp_path: Path) -> None:
    context = build_crafting_card_context(make_result(), StubAssets())
    too_large = StubRenderer({quality: 2000 for quality in QUALITY_LADDER})
    assert await render_crafting_card(too_large, context, target_bytes=100, hard_limit_bytes=1000) is None

    failing = StubRenderer({}, error=RenderError("boom"))
    assert await render_crafting_card(failing, context) is None
    assert await render_crafting_card(None, context) is None
