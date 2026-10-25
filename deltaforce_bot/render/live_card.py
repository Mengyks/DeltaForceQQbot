"""直播开播卡片的上下文装配与渲染。

原型（assets/items/html_prototype/直播订阅原型.png）：
- 顶部：主播名称 -- 直播状态（开播用粉色）
- 中间：直播封面；取不到封面时显示「未成功获取封面」
- 底部：直播间地址

图片用 JPEG 输出，先按 720px 宽渲染；超过目标体积时自动降低质量，
避免 QQ/OneBot 单帧限制，也避免在有限的云服务器存储上留下大文件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from deltaforce_bot.models.live import LIVE_STATUS_LIVE, LIVE_STATUS_ROUND
from deltaforce_bot.render.assets import file_url
from deltaforce_bot.render.renderer import CardRenderer, RenderError

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "live_notification.html.j2"
CARD_VIEWPORT_WIDTH = 720
LIVE_PINK = "#FB7299"
OFFLINE_GRAY = "#9aa5b1"

DEFAULT_TARGET_BYTES = 350 * 1024
DEFAULT_HARD_LIMIT_BYTES = 600 * 1024
QUALITY_LADDER = (86, 78, 70, 62)


@dataclass(frozen=True)
class LiveCardContext:
    uname: str
    status_text: str
    status_color: str
    cover_url: str | None
    room_url: str
    cover_missing: bool


def build_live_card_context(
    *,
    uname: str,
    live_status: int,
    room_id: str,
    cover_path: Path | None = None,
) -> LiveCardContext:
    if live_status == LIVE_STATUS_LIVE:
        status_text = "直播中"
        status_color = LIVE_PINK
    elif live_status == LIVE_STATUS_ROUND:
        status_text = "轮播"
        status_color = OFFLINE_GRAY
    else:
        status_text = "未开播"
        status_color = OFFLINE_GRAY

    cover_url = file_url(cover_path) if cover_path is not None and cover_path.exists() else None
    room_url = f"https://live.bilibili.com/{room_id}" if room_id else ""
    return LiveCardContext(
        uname=uname or "B站主播",
        status_text=status_text,
        status_color=status_color,
        cover_url=cover_url,
        room_url=room_url,
        cover_missing=cover_url is None,
    )


async def render_live_card(
    renderer: CardRenderer | None,
    context: LiveCardContext,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    hard_limit_bytes: int = DEFAULT_HARD_LIMIT_BYTES,
) -> bytes | None:
    """渲染直播卡片；按质量阶梯压缩，失败返回 None 让调用方回退文字。"""
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
            logger.warning("直播卡片渲染失败（质量 %s）：%s", quality, exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "直播卡片渲染异常（质量 %s）：%s: %s",
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
                "直播卡片渲染完成：%s KB（JPEG 质量 %s）",
                len(image) // 1024,
                quality,
            )
            return image

    if best is None:
        logger.warning("直播卡片渲染失败，改用文字")
        return None
    if len(best) <= hard_limit_bytes:
        logger.info(
            "直播卡片压缩到 %s KB（JPEG 质量 %s，未达目标但低于硬上限）",
            len(best) // 1024,
            best_quality,
        )
        return best

    logger.warning(
        "直播卡片压缩后仍然过大（>%s KB），改用文字",
        hard_limit_bytes // 1024,
    )
    return None
