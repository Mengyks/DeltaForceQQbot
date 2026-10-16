"""下载装备图片资源到本地，供渲染卡片使用。

图片源：数据源返回的 `pic` 字段——**腾讯官方 CDN**（playerhub.df.qq.com）。
比去第三方 wiki 抓图可靠得多：官方、稳定、无防盗链、国内速度快。

用法（在项目根目录）：

    .venv/Scripts/python.exe scripts/fetch_item_images.py                    # 从本机 fixture 里收集装备并下载
    .venv/Scripts/python.exe scripts/fetch_item_images.py --from-api         # 直接调数据源全量接口（消耗 20 次资源）
    .venv/Scripts/python.exe scripts/fetch_item_images.py --urls extra.txt  # 追加自定义「名称<TAB>图片地址」

产物：

    assets/items/<装备名>.png      文件名即装备名（非法字符替换为 _）
    assets/items/index.json        名称 → {object_id, url, file, size}，供渲染查表
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.services.gear_loadout import parse_groups  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "assets" / "items"
REAL_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "real"

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize(name: str) -> str:
    """把装备名变成合法文件名（保留中文与空格，替换非法字符）。"""
    cleaned = _ILLEGAL.sub("_", name).strip().rstrip(".")
    return cleaned or "unnamed"


def object_id_from_url(url: str) -> str:
    match = re.search(r"/object/(?:p_)?(\d+)\.png", url or "")
    return match.group(1) if match else ""


def collect_from_fixtures() -> dict[str, str]:
    """从本机录制的真实响应里收集「装备名 → 图片地址」。"""
    items: dict[str, str] = {}
    for fixture in sorted(REAL_FIXTURES.glob("jzv4_zb_lv*.json")):
        body = json.loads(fixture.read_text(encoding="utf-8"))
        for group in parse_groups(body.get("data")):
            for plan in group.plans:
                for item in plan.items:
                    if item.pic:
                        items[item.name] = item.pic
    return items


async def collect_from_api() -> dict[str, str]:
    """调数据源全物品接口拿全量图片地址（消耗 20 次资源，谨慎调用）。"""
    settings = get_settings()
    if not settings.orzice_token:
        raise SystemExit("未配置 ORZICE_TOKEN")
    async with httpx.AsyncClient(base_url=settings.orzice_base_url, timeout=60) as client:
        response = await client.get("/v1/sjz_api/item_info_all", params={"token": settings.orzice_token})
    if response.status_code >= 400:
        raise SystemExit(f"接口返回 HTTP {response.status_code}")
    body = response.json()
    items: dict[str, str] = {}
    for entry in body.get("data") or []:
        name, pic = entry.get("objectName"), entry.get("pic")
        if name and pic:
            items[str(name)] = str(pic)
    print(f"接口返回 {len(items)} 件物品的图片地址")
    return items


async def download_all(items: dict[str, str], out_dir: Path, *, force: bool, concurrency: int = 8) -> tuple[int, int, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.json"
    index: dict[str, dict[str, object]] = {}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))

    semaphore = asyncio.Semaphore(concurrency)
    ok = skipped = 0
    failures: list[str] = []

    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "Mozilla/5.0 (DeltaForceQQbot asset fetcher)"}) as client:

        async def fetch(name: str, url: str) -> None:
            nonlocal ok, skipped
            filename = f"{sanitize(name)}.png"
            target = out_dir / filename
            if target.exists() and not force and target.stat().st_size > 0:
                skipped += 1
                index[name] = {"object_id": object_id_from_url(url), "url": url, "file": filename, "size": target.stat().st_size}
                return
            async with semaphore:
                try:
                    response = await client.get(url)
                    if response.status_code != 200 or not response.content.startswith(b"\x89PNG"):
                        failures.append(f"{name}: HTTP {response.status_code} / 非 PNG")
                        return
                    target.write_bytes(response.content)
                    ok += 1
                    index[name] = {"object_id": object_id_from_url(url), "url": url, "file": filename, "size": len(response.content)}
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{name}: {exc.__class__.__name__}")

        await asyncio.gather(*(fetch(name, url) for name, url in items.items()))

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, skipped, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="下载装备图片资源")
    parser.add_argument("--from-api", action="store_true", help="改用全物品接口（消耗 20 次资源）")
    parser.add_argument("--urls", type=Path, help="额外清单文件，每行「名称<TAB>图片地址」")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true", help="已存在的文件也重新下载")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    if args.from_api:
        items = asyncio.run(collect_from_api())
    else:
        items = collect_from_fixtures()
        print(f"从本机 fixture 收集到 {len(items)} 件装备")

    if args.urls and args.urls.exists():
        for line in args.urls.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                name, url = line.split("\t", 1)
                items[name.strip()] = url.strip()
        print(f"合并清单后共 {len(items)} 件")

    if not items:
        raise SystemExit("没有收集到任何装备图片地址")

    ok, skipped, failures = asyncio.run(download_all(items, args.out, force=args.force, concurrency=args.concurrency))
    print(f"\n下载完成：新下载 {ok} 张，已存在跳过 {skipped} 张，失败 {len(failures)} 张")
    print(f"图片目录：{args.out}")
    if failures:
        print("失败列表（前 10 条）：")
        for line in failures[:10]:
            print("  -", line)


if __name__ == "__main__":
    main()
