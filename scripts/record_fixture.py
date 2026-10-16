"""录制第三方接口的真实响应到 tests/fixtures/，用于契约测试。

用法（在项目根目录，token 从 .env 读取）：

    python scripts/record_fixture.py --path /v1/sjz_api/jzv4_zb --param lv=0 --out tests/fixtures/jzv4_zb_lv0.json
    python scripts/record_fixture.py --path /v1/sjz_api/jzv3_diy --param zb=50000 --out tests/fixtures/jzv3_diy.json

注意：录制会真实消耗平台资源次数（V4 接口 1 次，DIY 接口 10 次，全量接口 20 次），
录制出来的文件里不应包含个人隐私信息。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402


def parse_params(pairs: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"参数格式应为 key=value：{pair}")
        key, value = pair.split("=", 1)
        params[key] = value
    return params


async def record(path: str, params: dict[str, str], out: Path) -> None:
    settings = get_settings()
    if not settings.orzice_token:
        raise SystemExit("未配置 ORZICE_TOKEN（请在项目根目录创建 .env）")

    async with httpx.AsyncClient(base_url=settings.orzice_base_url, timeout=30) as client:
        try:
            response = await client.get(path, params={**params, "token": settings.orzice_token})
        except httpx.HTTPError as exc:
            # 不要把含 token 的 URL 打进日志/终端
            raise SystemExit(f"请求失败：{exc.__class__.__name__}") from None
        if response.status_code >= 400:
            raise SystemExit(f"数据源返回 HTTP {response.status_code}（路径或权限有问题）")
        body = response.json()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    data = body.get("data")
    size = len(data) if isinstance(data, (list, dict)) else 0
    print(f"已写入 {out}（code={body.get('code')}，data 条目数 {size}）")
    print("下一步：核对 docs/01-卡战备-功能设计.md §2.3 与 tests/fixtures/README.md 里列出的待确认字段。")


def main() -> None:
    parser = argparse.ArgumentParser(description="录制数据帝接口响应作为测试 fixture")
    parser.add_argument("--path", required=True, help="接口路径，如 /v1/sjz_api/jzv4_zb")
    parser.add_argument("--param", action="append", default=[], help="业务参数 key=value，可重复")
    parser.add_argument("--out", required=True, type=Path, help="输出文件路径")
    args = parser.parse_args()

    asyncio.run(record(args.path, parse_params(args.param), args.out))


if __name__ == "__main__":
    main()
