"""pytest 公共配置：插件加载类测试需要 NoneBot 处于已初始化状态。"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_nonebot() -> None:
    import nonebot

    try:
        nonebot.get_driver()
    except Exception:
        nonebot.init(driver="~fastapi+~httpx+~websockets")
        from nonebot.adapters.onebot.v11 import Adapter

        nonebot.get_driver().register_adapter(Adapter)
