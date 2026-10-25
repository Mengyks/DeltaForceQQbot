"""本地预览直播开播卡片（会请求一次 B 站公开接口，不需要登录）。

    python scripts/render_live_preview.py
    python scripts/render_live_preview.py --uid 672328094 --out assets/_preview

产物为 JPEG，默认保存到 assets/_preview/live_subscription_preview.jpg。
即使主播当前未开播，也会按“直播中”样式渲染，便于检查卡片版式。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.adapters.live_bilibili import BilibiliLiveClient  # noqa: E402
from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.models.live import LIVE_STATUS_LIVE, LiveStatus  # noqa: E402
from deltaforce_bot.render.live_card import (  # noqa: E402
    build_live_card_context,
    render_live_card,
)
from deltaforce_bot.render.renderer import CardRenderer  # noqa: E402


async def main(uid: str, out_dir: Path, output_name: str) -> None:
    settings = get_settings()
    client = BilibiliLiveClient(settings)
    renderer = CardRenderer(
        timeout=max(settings.render_timeout, 20.0),
        chromium_path=settings.render_chromium_path,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        status = await client.fetch_status(uid)
        if status is None:
            print(f"! UID {uid} 没有返回直播间，使用占位封面")
            status = LiveStatus(uid=uid, room_id="", uname="示例主播")

        with tempfile.TemporaryDirectory(prefix="deltaforce-live-preview-") as tmp:
            cover_path = None
            if status.cover:
                cover_path = await client.download_cover(status.cover, Path(tmp) / "cover.jpg")
            context = build_live_card_context(
                uname=status.uname or uid,
                live_status=LIVE_STATUS_LIVE,  # 预览开播样式
                room_id=status.room_id or "1883376823",
                cover_path=cover_path,
            )
            image = await render_live_card(
                renderer,
                context,
                target_bytes=max(settings.live_image_max_kb, 50) * 1024,
            )

        if image is None:
            print("× 卡片渲染失败")
            raise SystemExit(1)
        path = out_dir / output_name
        path.write_bytes(image)
        print(f"√ 直播开播卡片：{len(image) // 1024} KB → {path}")
        if context.cover_missing:
            print("! 没有拿到封面，卡片使用了「未成功获取封面」占位")
    finally:
        await renderer.close()
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="直播开播卡片预览")
    parser.add_argument("--uid", default="672328094", help="B站 UID；默认嘉然")
    parser.add_argument("--out", type=Path, default=Path("assets/_preview"))
    parser.add_argument("--name", default="live_subscription_preview.jpg")
    args = parser.parse_args()
    asyncio.run(main(args.uid, args.out, args.name))
