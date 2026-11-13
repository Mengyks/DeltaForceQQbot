"""每日天尊挑战：HTML 上下文、模板字段与 JPEG 质量阶梯。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.adapters.daily_challenge_assets import ChallengeAsset
from deltaforce_bot.render.daily_challenge_card import (
    QUALITY_LADDER,
    build_daily_challenge_context,
    render_daily_challenge,
)
from deltaforce_bot.render.renderer import TEMPLATE_DIR, RenderError


def make_asset(tmp_path: Path, name: str) -> ChallengeAsset:
    path = tmp_path / f"{name}.png"
    path.write_bytes(b"fake-png")
    return ChallengeAsset(name=name, path=path)


def make_context(tmp_path: Path):
    background = tmp_path / "background.jpeg"
    background.write_bytes(b"fake-jpeg")
    return build_daily_challenge_context(
        hunter_name="群友A",
        monster=make_asset(tmp_path, "灭尽龙"),
        weapon=make_asset(tmp_path, "大剑"),
        background=background,
        avatar_data=b"\xff\xd8\xffavatar",
    )


def test_context_contains_names_and_urls(tmp_path: Path) -> None:
    context = make_context(tmp_path)

    assert context.hunter_name == "群友A"
    assert context.monster_name == "灭尽龙"
    assert context.weapon_name == "大剑"
    assert context.avatar_url.startswith("data:image/jpeg;base64,")
    assert context.background_url is not None
    assert context.background_url.startswith("file:///")
    assert context.monster_url is not None and context.monster_url.startswith("file:///")
    assert context.weapon_url is not None and context.weapon_url.startswith("file:///")


def test_template_renders_fields(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )

    html = env.get_template("daily_challenge.html.j2").render(**context.__dict__)

    assert "每日天尊挑战" in html
    assert "群友A" in html
    assert "灭尽龙" in html
    assert "大剑" in html
    assert context.avatar_url in html
    assert "{{" not in html


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


async def test_quality_ladder_until_target(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    renderer = StubRenderer({90: 900, 84: 500, 78: 200})

    image = await render_daily_challenge(
        renderer, context, target_bytes=300, hard_limit_bytes=1000
    )

    assert image is not None and len(image) == 200
    assert [call["quality"] for call in renderer.calls] == [90, 84, 78]
    assert all(call["screenshot_type"] == "jpeg" for call in renderer.calls)
    assert all(call["viewport_width"] == 1600 for call in renderer.calls)


async def test_quality_ladder_returns_first_under_target(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    renderer = StubRenderer({90: 100})

    image = await render_daily_challenge(
        renderer, context, target_bytes=300, hard_limit_bytes=1000
    )

    assert image is not None and len(image) == 100
    assert len(renderer.calls) == 1


async def test_too_large_returns_none(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    renderer = StubRenderer({quality: 2000 for quality in QUALITY_LADDER})

    assert await render_daily_challenge(
        renderer, context, target_bytes=100, hard_limit_bytes=1000
    ) is None


async def test_render_error_returns_none(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    renderer = StubRenderer({}, error=RenderError("boom"))

    assert await render_daily_challenge(renderer, context) is None


async def test_none_renderer_returns_none(tmp_path: Path) -> None:
    context = make_context(tmp_path)

    assert await render_daily_challenge(None, context) is None
