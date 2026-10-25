"""本地预览制造台利润卡片（用录制好的真实响应，不请求数据帝）。

    python scripts/render_crafting_preview.py
    python scripts/render_crafting_preview.py --out assets/_preview

产物为 JPEG，默认保存到 assets/_preview/crafting_profit_preview.jpg。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.render.crafting_assets import CraftingAssetLibrary  # noqa: E402
from deltaforce_bot.render.crafting_card import (  # noqa: E402
    build_crafting_card_context,
    render_crafting_card,
)
from deltaforce_bot.render.renderer import CardRenderer  # noqa: E402
from deltaforce_bot.services.crafting import CraftingService  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "real"


class FixtureClient:
    """用 tests/fixtures/real 里的真实响应冒充 OrziceClient。"""

    def __init__(self, mapping: dict[int, dict]) -> None:
        self.mapping = mapping

    async def get(self, path: str, *, params: dict | None = None, ttl: int, cost: int = 1):
        station = int((params or {}).get("t") or 0)
        body = self.mapping[station]
        return body["data"]


async def main(out_dir: Path, output_name: str) -> None:
    settings = get_settings()
    mapping = {
        station: json.loads(
            (FIXTURES / f"crafting_t{station}_l3.json").read_text(encoding="utf-8")
        )
        for station in (1, 2, 3, 4)
    }
    assets = CraftingAssetLibrary(settings.assets_dir)
    print(f"资源索引：{assets.stats}")
    service = CraftingService(settings, FixtureClient(mapping))  # type: ignore[arg-type]
    renderer = CardRenderer(
        timeout=max(settings.render_timeout, 20.0),
        chromium_path=settings.render_chromium_path,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = await service.get_profit()
        context = build_crafting_card_context(
            result,
            assets,
            cache_hours=max(int(settings.crafting_cache_ttl), 60) // 3600,
        )
        image = await render_crafting_card(
            renderer,
            context,
            target_bytes=max(int(settings.crafting_image_max_kb), 50) * 1024,
        )
        if image is None:
            print("× 卡片渲染失败")
            raise SystemExit(1)
        path = out_dir / output_name
        path.write_bytes(image)
        print(f"√ 制造台利润卡片：{len(image) // 1024} KB → {path}")
        for station in result.stations:
            if station.item is None:
                print(f"! {station.label}：{station.error}")
            else:
                item = station.item
                status = "负利润" if item.price < 0 else "正利润"
                print(
                    f"  {station.label}：{item.name} "
                    f"单次 {item.price:,} / 每小时 {item.price_hour:,}（{status}）"
                )
    finally:
        await renderer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="制造台利润卡片预览")
    parser.add_argument("--out", type=Path, default=Path("assets/_preview"))
    parser.add_argument("--name", default="crafting_profit_preview.jpg")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.name))
