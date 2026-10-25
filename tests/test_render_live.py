"""直播卡片：上下文装配、模板字段、压缩阶梯。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.render.live_card import (
    LIVE_PINK,
    QUALITY_LADDER,
    LiveCardContext,
    build_live_card_context,
    render_live_card,
)
from deltaforce_bot.render.renderer import TEMPLATE_DIR, RenderError


def make_context(tmp_path: Path, *, with_cover: bool) -> LiveCardContext:
    cover = None
    if with_cover:
        cover = tmp_path / "封面.jpg"
        cover.write_bytes(b"fake")
    return build_live_card_context(
        uname="测试主播",
        live_status=1,
        room_id="1883376823",
        cover_path=cover,
    )


def test_context_live_status_and_room_url(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_cover=True)

    assert context.uname == "测试主播"
    assert context.status_text == "直播中"
    assert context.status_color == LIVE_PINK
    assert context.room_url == "https://live.bilibili.com/1883376823"
    assert context.cover_url is not None
    assert context.cover_url.startswith("file:///")
    assert "%E5%B0%81%E9%9D%A2" in context.cover_url  # 「封面」被 URL 编码


def test_context_without_cover_uses_placeholder(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_cover=False)

    assert context.cover_url is None
    assert context.cover_missing is True


def test_template_contains_required_fields(tmp_path: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("live_notification.html.j2")

    html = template.render(**make_context(tmp_path, with_cover=False).__dict__)
    assert "测试主播" in html
    assert "直播中" in html
    assert "未成功获取封面" in html
    assert "https://live.bilibili.com/1883376823" in html
    assert LIVE_PINK in html

    html_with_cover = template.render(**make_context(tmp_path, with_cover=True).__dict__)
    assert "未成功获取封面" not in html_with_cover
    assert "file:///" in html_with_cover


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


async def test_render_uses_quality_ladder_until_under_target(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_cover=False)
    renderer = StubRenderer({86: 900, 78: 500, 70: 200})

    image = await render_live_card(renderer, context, target_bytes=300, hard_limit_bytes=1000)

    assert image is not None and len(image) == 200
    assert [call["quality"] for call in renderer.calls] == [86, 78, 70]
    assert all(call["viewport_width"] == 720 for call in renderer.calls)
    assert all(call["screenshot_type"] == "jpeg" for call in renderer.calls)


async def test_render_returns_smallest_when_target_not_met_but_under_hard_limit(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_cover=False)
    renderer = StubRenderer({quality: 800 - index * 100 for index, quality in enumerate(QUALITY_LADDER)})

    image = await render_live_card(renderer, context, target_bytes=100, hard_limit_bytes=900)

    assert image is not None
    assert len(image) == renderer.sizes[QUALITY_LADDER[-1]]
    assert len(renderer.calls) == len(QUALITY_LADDER)


async def test_render_returns_none_when_too_large_or_failed(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_cover=False)

    too_large = StubRenderer({quality: 2000 for quality in QUALITY_LADDER})
    assert await render_live_card(too_large, context, target_bytes=100, hard_limit_bytes=1000) is None

    failing = StubRenderer({}, error=RenderError("boom"))
    assert await render_live_card(failing, context) is None

    assert await render_live_card(None, context) is None
