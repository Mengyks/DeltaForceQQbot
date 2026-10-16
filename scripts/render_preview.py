"""本地预览图片卡片（用录制的真实响应渲染，不需要 token、不联网）。

    python scripts/render_preview.py                       # 渲染默认几个档位
    python scripts/render_preview.py "巴克什 绝密" "az3 机密"
    python scripts/render_preview.py --out assets/_preview

产物保存为 PNG，直接打开即可看效果；图片目录取自 .env 的 ASSETS_DIR（默认 assets/items）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.models.gate import resolve_gate  # noqa: E402
from deltaforce_bot.render.assets import AssetLibrary  # noqa: E402
from deltaforce_bot.render.card import build_card_context  # noqa: E402
from deltaforce_bot.render.renderer import CardRenderer  # noqa: E402
from deltaforce_bot.services.gear_loadout import GearLoadoutService  # noqa: E402
from deltaforce_bot.services.text import format_cache_note  # noqa: E402
from tests.support import load_data  # noqa: E402

DEFAULT_TARGETS = ["巴克什 绝密", "az3 机密"]


def _cleanup_stale(out_dir: Path, ttl_seconds: int) -> None:
    """删掉超过缓存时长的预览图（用户要求：图片不要一直保存）。"""
    import time

    cutoff = time.time() - ttl_seconds
    removed = 0
    for png in out_dir.glob("*.png"):
        if png.stat().st_mtime < cutoff:
            png.unlink(missing_ok=True)
            removed += 1
    if removed:
        print(f"已清理 {removed} 张过期预览图（超过 {ttl_seconds // 3600} 小时）")


class FixtureClient:
    """按 lv 返回本机录制的真实响应。"""

    async def get(self, path: str, *, params: dict[str, Any] | None = None, ttl: int, cost: int = 1) -> Any:
        if path.endswith("jzv4_zb"):
            lv = (params or {}).get("lv", 0)
            return load_data(f"jzv4_zb_lv{lv}.json", real=True)
        raise AssertionError(f"预览脚本只支持卡战备接口：{path}")

    def cache_age(self, path: str, params: dict[str, Any] | None = None) -> float | None:
        return None


async def main(targets: list[str], out_dir: Path, include_exchange: bool) -> None:
    settings = get_settings()
    assets = AssetLibrary(settings.assets_dir)
    print(f"资源索引：装备图 {assets.stats[0]} 张、背景图 {assets.stats[1]} 张")
    renderer = CardRenderer(timeout=settings.render_timeout, chromium_path=settings.render_chromium_path)
    service = GearLoadoutService(FixtureClient(), settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale(out_dir, settings.image_cache_ttl)

    from deltaforce_bot.models.gate import load_gate_config

    gates_config = load_gate_config(settings.gates_config)

    try:
        for target in targets:
            parts = target.split()
            resolution = resolve_gate(gates_config, parts[0], parts[1] if len(parts) > 1 else None)
            if not resolution.ok or resolution.gate is None:
                print(f"× 无法解析「{target}」：{resolution.error}")
                continue
            gate = resolution.gate
            result = await service.by_gate(gate, include_exchange=include_exchange, top=settings.alternatives_default)
            if result.best is None:
                print(f"× {target} 没有方案数据")
                continue
            context = build_card_context(
                plan=result.best,
                gate_label=gate.label,
                map_name=gate.map,
                difficulty=gate.difficulty,
                threshold=result.threshold,
                updated_at=result.updated_at,
                cache_note=format_cache_note(result.cache_age),
                alternatives=result.alternatives,
                include_exchange=include_exchange,
                assets=assets,
            )
            png = await renderer.render(context)
            path = out_dir / f"card_{gate.map}_{gate.difficulty}.png"
            path.write_bytes(png)
            print(f"√ {gate.label}：{len(png) // 1024} KB → {path}")
    finally:
        await renderer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本地预览卡片渲染")
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS, help="如「巴克什 绝密」")
    parser.add_argument("--out", type=Path, default=Path("assets/_preview"))
    parser.add_argument("--exchange", action="store_true", help="包含军需处兑换方案")
    args = parser.parse_args()
    asyncio.run(main(args.targets or DEFAULT_TARGETS, args.out, args.exchange))
