"""用 fixture 数据跑一遍完整命令链路，预览群里的实际回复文本。

不需要 token、不联网、不消耗平台资源，用于调排版：

    python scripts/demo_reply.py "卡战备 巴克什 绝密"
    python scripts/demo_reply.py            # 跑一组默认命令

数据来自 tests/fixtures/real/ 下**真实录制**的接口响应（2026-10-15），
因此排版效果与线上一致；价格会随游戏版本变化，但结构不变。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltaforce_bot.config import get_settings  # noqa: E402
from deltaforce_bot.runtime import Runtime  # noqa: E402
from deltaforce_bot.services.commands import handle_gear_command  # noqa: E402
from deltaforce_bot.services.gear_loadout import GearLoadoutService  # noqa: E402
from tests.support import load_data  # noqa: E402

DEFAULT_TEXTS = [
    "卡战备",
    "卡战备 巴克什 绝密",
    "卡战备 航天 绝密 兑换",
    "卡战备 大坝 机密",
    "凑战备 5万",
]


class FixtureClient:
    """按接口路径与档位返回真实录制的响应，替代真实 OrziceClient。"""

    async def get(self, path: str, *, params: dict[str, Any] | None = None, ttl: int, cost: int = 1) -> Any:
        if path.endswith("jzv4_zb"):
            lv = (params or {}).get("lv", 0)
            return load_data(f"jzv4_zb_lv{lv}.json", real=True)
        if path.endswith("jzv3_diy"):
            return load_data("jzv3_diy.json")
        raise AssertionError(f"fixture 里没有这个接口：{path}")

    def cache_age(self, path: str, params: dict[str, Any] | None = None) -> float | None:
        return None  # 预览时不展示缓存时长


async def main(texts: list[str]) -> None:
    runtime = Runtime(get_settings())
    runtime.gear_loadout = GearLoadoutService(FixtureClient(), runtime.settings)
    runtime.debug_groups = {0}  # 预览工具：跳过限流，方便连续看多条

    for text in texts:
        print("=" * 64)
        print(f"用户：{text}")
        print("-" * 64)
        reply = await handle_gear_command(runtime, text, group_id=0, user_id=1)
        print(reply if reply is not None else "（不是本插件的命令，机器人不回应）")
        print()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_TEXTS))
