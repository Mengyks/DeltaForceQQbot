"""本地预览每日密码卡片（使用当天免费数据源/缓存，不需要 token）。

    python scripts/render_daily_password_preview.py
    python scripts/render_daily_password_preview.py --out assets/_preview

产物为 JPEG，默认保存到 assets/_preview/daily_password_preview.jpg。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.adapters.daily_password import DailyPasswordClient  # noqa: E402
from deltaforce_bot.adapters.daily_password_store import DailyPasswordStore  # noqa: E402
from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.render.assets import AssetLibrary  # noqa: E402
from deltaforce_bot.render.daily_password_card import build_daily_password_context  # noqa: E402
from deltaforce_bot.render.renderer import CardRenderer  # noqa: E402
from deltaforce_bot.services.daily_password import (  # noqa: E402
    DailyPasswordService,
    DailyPasswordUnavailable,
)


async def main(out_dir: Path, output_name: str) -> None:
    settings = get_settings()
    assets = AssetLibrary(settings.assets_dir)
    print(f"资源索引：装备图 {assets.stats[0]} 张、背景图 {assets.stats[1]} 张")

    store = DailyPasswordStore(settings.daily_password_db_path)
    client = DailyPasswordClient(settings)
    service = DailyPasswordService(settings, client, store)
    renderer = CardRenderer(
        timeout=max(settings.render_timeout, 20.0),
        chromium_path=settings.render_chromium_path,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        snapshot = await service.get_today()
        context = build_daily_password_context(
            snapshot,
            assets,
            push_time=service.push_time_text,
        )
        image_bytes = await renderer.render_template(
            context,
            template_name="daily_password.html.j2",
            screenshot_type="jpeg",
            quality=90,
        )
        path = out_dir / output_name
        path.write_bytes(image_bytes)
        print(f"√ 每日密码卡片：{len(image_bytes) // 1024} KB → {path}")
        missing = [entry.name for entry in context.entries if not entry.background_url]
        if missing:
            print(f"! 以下地图没有背景图，使用纯色兜底：{', '.join(missing)}")
    except DailyPasswordUnavailable as exc:
        print(f"× 今日密码暂时不可用：{exc}")
        raise SystemExit(1) from exc
    finally:
        await renderer.close()
        await service.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日密码卡片预览")
    parser.add_argument("--out", type=Path, default=Path("assets/_preview"))
    parser.add_argument("--name", default="daily_password_preview.jpg")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.name))
