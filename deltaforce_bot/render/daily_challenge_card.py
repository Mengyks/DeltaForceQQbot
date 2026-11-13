"""每日天尊挑战卡片：HTML 模板 + Playwright 截图 + JPEG 质量阶梯。"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from deltaforce_bot.adapters.daily_challenge_assets import ChallengeAsset
from deltaforce_bot.render.assets import file_url
from deltaforce_bot.render.renderer import CardRenderer, RenderError

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "daily_challenge.html.j2"
CARD_VIEWPORT_WIDTH = 1600
QUALITY_LADDER = (90, 84, 78, 72)
DEFAULT_TARGET_BYTES = 350 * 1024
DEFAULT_HARD_LIMIT_BYTES = 600 * 1024


@dataclass
class DailyChallengeContext:
    hunter_name: str
    monster_name: str
    weapon_name: str
    avatar_url: str
    background_url: str | None
    monster_url: str | None
    weapon_url: str | None


def _avatar_data_uri(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"RIFF"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_daily_challenge_context(
    *,
    hunter_name: str,
    monster: ChallengeAsset,
    weapon: ChallengeAsset,
    background: Path | None,
    avatar_data: bytes,
) -> DailyChallengeContext:
    return DailyChallengeContext(
        hunter_name=hunter_name,
        monster_name=monster.name,
        weapon_name=weapon.name,
        avatar_url=_avatar_data_uri(avatar_data),
        background_url=file_url(background),
        monster_url=file_url(monster.path),
        weapon_url=file_url(weapon.path),
    )


async def render_daily_challenge(
    renderer: CardRenderer | None,
    context: DailyChallengeContext,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    hard_limit_bytes: int = DEFAULT_HARD_LIMIT_BYTES,
) -> bytes | None:
    """渲染挑战图；按质量阶梯压缩，失败返回 None。"""
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
            logger.warning("每日天尊挑战渲染失败（质量 %s）：%s", quality, exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "每日天尊挑战渲染异常（质量 %s）：%s: %s",
                quality,
                exc.__class__.__name__,
                exc,
            )
            break

        if not image:
            logger.warning("每日天尊挑战渲染返回空图片（质量 %s）", quality)
            break
        if best is None or len(image) < len(best):
            best = image
            best_quality = quality
        if len(image) <= target_bytes:
            logger.info(
                "每日天尊挑战渲染完成：%s KB（JPEG 质量 %s）",
                len(image) // 1024,
                quality,
            )
            return image

    if best is None:
        logger.warning("每日天尊挑战渲染失败")
        return None
    if len(best) <= hard_limit_bytes:
        logger.info(
            "每日天尊挑战压缩到 %s KB（JPEG 质量 %s，未达目标但低于硬上限）",
            len(best) // 1024,
            best_quality,
        )
        return best

    logger.warning(
        "每日天尊挑战压缩后仍然过大（>%s KB）",
        hard_limit_bytes // 1024,
    )
    return None
