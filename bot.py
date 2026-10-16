"""启动入口。

本机开发：在项目根目录执行 ``python bot.py``（Windows 下 ``.venv\\Scripts\\python.exe bot.py``）。
NapCat 以反向 WebSocket 连到本进程，地址形如 ``ws://127.0.0.1:8080/onebot/v11/ws``。
"""

from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(Adapter)

# 把标准库 logging 接到 loguru（NoneBot 2.5 不再自动接管），否则业务日志看不到
from deltaforce_bot.logging_setup import setup_logging  # noqa: E402

setup_logging()

nonebot.load_from_toml("pyproject.toml")

# 启动即装配运行时（同时校验档位配置是否可读、token 是否配置）
from deltaforce_bot.runtime import get_runtime  # noqa: E402

runtime = get_runtime()

# 掉线监控：掉线写日志/推 webhook，恢复后私聊通知管理员（QQ 掉线时无法通过 QQ 通知自己）
from deltaforce_bot.watchdog import install_watchdog  # noqa: E402

install_watchdog(runtime.settings)


@driver.on_shutdown
async def _release_data_source() -> None:
    await runtime.aclose()


if __name__ == "__main__":
    nonebot.run()
