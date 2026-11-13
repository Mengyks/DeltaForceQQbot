"""帮助页卡片的上下文装配与渲染。

原型：assets/items/html_prototype/帮助命令原型.png
背景：assets/items/backgrounds/帮助窗口背景.jpg
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from deltaforce_bot.render.assets import file_url
from deltaforce_bot.render.renderer import CardRenderer, RenderError
from deltaforce_bot.services.help import (
    HELP_FOOTER,
    HELP_SECTIONS,
    HELP_SUBTITLE,
    HELP_TITLE,
)

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "help_card.html.j2"
CARD_VIEWPORT_WIDTH = 1000
DEFAULT_TARGET_BYTES = 350 * 1024
DEFAULT_HARD_LIMIT_BYTES = 600 * 1024
QUALITY_LADDER = (86, 80, 72, 64)
HELP_BACKGROUND_NAME = "帮助窗口背景.jpg"


@dataclass(frozen=True)
class HelpCardCommand:
    command: str
    example: str


@dataclass(frozen=True)
class HelpCardSection:
    title: str
    badge: str
    accent: str
    commands: list[HelpCardCommand]
    note: str


@dataclass(frozen=True)
class HelpCardContext:
    title: str
    subtitle: str
    footer: str
    background_url: str | None
    sections: list[HelpCardSection]


def build_help_card_context(background_path: Path | None) -> HelpCardContext:
    background_url = None
    if background_path is not None and background_path.exists():
        background_url = file_url(background_path)
    else:
        logger.warning("帮助页背景图不存在，使用渐变背景")

    sections = [
        HelpCardSection(
            title=section.title,
            badge=section.badge,
            accent=section.accent,
            commands=[
                HelpCardCommand(command=command.command, example=command.example)
                for command in section.commands
            ],
            note=section.note,
        )
        for section in HELP_SECTIONS
    ]
    return HelpCardContext(
        title=HELP_TITLE,
        subtitle=HELP_SUBTITLE,
        footer=HELP_FOOTER,
        background_url=background_url,
        sections=sections,
    )


async def render_help_card(
    renderer: CardRenderer | None,
    context: HelpCardContext,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    hard_limit_bytes: int = DEFAULT_HARD_LIMIT_BYTES,
) -> bytes | None:
    """渲染帮助卡片；按质量阶梯压缩，失败返回 None。"""
    if renderer is None:
        return None

    best: bytes | None = None
    best_quality = 0
    for quality in QUALITY_LADDER:
        try:
            image = await renderer.render_template(
                context,
                template_name=TEMPLATE_NAME,
                screenshot_type="jpeg",
                quality=quality,
                viewport_width=CARD_VIEWPORT_WIDTH,
            )
        except RenderError as exc:
            logger.warning("帮助卡片渲染失败（质量 %s）：%s", quality, exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "帮助卡片渲染异常（质量 %s）：%s: %s",
                quality,
                exc.__class__.__name__,
                exc,
            )
            break

        if best is None or len(image) < len(best):
            best = image
            best_quality = quality
        if len(image) <= target_bytes:
            logger.info(
                "帮助卡片渲染完成：%s KB（JPEG 质量 %s）",
                len(image) // 1024,
                quality,
            )
            return image

    if best is None:
        logger.warning("帮助卡片渲染失败，改用文字")
        return None
    if len(best) <= hard_limit_bytes:
        logger.info(
            "帮助卡片压缩到 %s KB（JPEG 质量 %s，未达目标但低于硬上限）",
            len(best) // 1024,
            best_quality,
        )
        return best

    logger.warning(
        "帮助卡片压缩后仍然过大（>%s KB），改用文字",
        hard_limit_bytes // 1024,
    )
    return None
