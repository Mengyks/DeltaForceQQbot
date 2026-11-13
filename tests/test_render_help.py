"""帮助页卡片：上下文、背景、模板字段与压缩阶梯。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.render.help_card import (
    QUALITY_LADDER,
    build_help_card_context,
    render_help_card,
)
from deltaforce_bot.render.renderer import TEMPLATE_DIR, RenderError


def make_context(tmp_path: Path, *, with_background: bool) -> object:
    background = None
    if with_background:
        background = tmp_path / "帮助窗口背景.jpg"
        background.write_bytes(b"fake-jpg")
    return build_help_card_context(background)


def test_context_uses_background_and_sections(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_background=True)

    assert context.title == "五点老师 · 帮助命令"
    assert "直接 @五点" in context.subtitle
    assert context.background_url is not None
    assert context.background_url.startswith("file:///")
    assert "%E5%B8%AE%E5%8A%A9" in context.background_url  # 「帮助」被 URL 编码
    assert [section.title for section in context.sections] == [
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
    assert context.sections[0].commands[0].command.startswith("/卡战备")
    assert context.sections[0].commands[0].example == "/卡战备 巴克什 绝密"
    assert context.sections[3].commands[0].example == "/直播订阅 添加 672328094"


def test_missing_background_falls_back(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_background=False)
    assert context.background_url is None


def test_template_contains_help_fields(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_background=True)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("help_card.html.j2").render(**context.__dict__)

    assert "五点老师 · 帮助命令" in html
    assert "HELP / COMMANDS" in html
    assert "/卡战备" in html
    assert "示例：/卡战备 巴克什 绝密" in html
    assert "/每日密码" in html
    assert "/制造利润" in html
    assert "/直播订阅 添加" in html
    assert "/waifu" in html
    assert "猪 / zhu / pig" in html
    assert "/每日天尊挑战" in html
    assert "feature wide" in html
    assert "@五点" in html
    assert "背景" not in html  # 模板里不应出现占位文案
    assert "background-image" in html
    assert "help_card" not in html


def test_missing_background_uses_gradient_class(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_background=False)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("help_card.html.j2").render(**context.__dict__)
    assert "no-image" in html
    assert "background-image" not in html


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
    context = make_context(tmp_path, with_background=True)
    renderer = StubRenderer({86: 900, 80: 500, 72: 200})

    image = await render_help_card(renderer, context, target_bytes=300, hard_limit_bytes=1000)

    assert image is not None and len(image) == 200
    assert [call["quality"] for call in renderer.calls] == [86, 80, 72]
    assert all(call["viewport_width"] == 1000 for call in renderer.calls)
    assert all(call["screenshot_type"] == "jpeg" for call in renderer.calls)


async def test_render_large_or_failed(tmp_path: Path) -> None:
    context = make_context(tmp_path, with_background=True)
    too_large = StubRenderer({quality: 2000 for quality in QUALITY_LADDER})
    assert await render_help_card(too_large, context, target_bytes=100, hard_limit_bytes=1000) is None

    failing = StubRenderer({}, error=RenderError("boom"))
    assert await render_help_card(failing, context) is None
    assert await render_help_card(None, context) is None
