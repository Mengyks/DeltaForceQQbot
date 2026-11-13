"""本地预览帮助页卡片（使用帮助窗口背景，不请求任何接口）。

    python scripts/render_help_preview.py
    python scripts/render_help_preview.py --out assets/_preview
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.render.help_card import (  # noqa: E402
    HELP_BACKGROUND_NAME,
    build_help_card_context,
    render_help_card,
)
from deltaforce_bot.render.renderer import CardRenderer  # noqa: E402


async def main(out_dir: Path, output_name: str) -> None:
    settings = get_settings()
    background = Path(settings.assets_dir) / "backgrounds" / HELP_BACKGROUND_NAME
    print(f"背景：{background}（存在：{background.exists()}）")
    context = build_help_card_context(background)
    renderer = CardRenderer(
        timeout=max(settings.render_timeout, 20.0),
        chromium_path=settings.render_chromium_path,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        image = await render_help_card(renderer, context)
        if image is None:
            print("× 帮助卡片渲染失败")
            raise SystemExit(1)
        path = out_dir / output_name
        path.write_bytes(image)
        print(f"√ 帮助卡片：{len(image) // 1024} KB → {path}")
    finally:
        await renderer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="帮助页卡片预览")
    parser.add_argument("--out", type=Path, default=Path("assets/_preview"))
    parser.add_argument("--name", default="help_preview.jpg")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.name))
