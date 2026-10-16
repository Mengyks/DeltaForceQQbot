"""把标准库 logging 接到 NoneBot 的 loguru 上。

NoneBot 2.5 不再自动接管标准库 logging：如果不做这一步，业务日志
（数据源请求、缓存命中、额度消耗）会落到 root logger 而没有任何 handler，
INFO 级别直接消失——上线后就看不到额度消耗了（这是真实踩到的坑）。
"""

from __future__ import annotations

import logging

from nonebot.log import logger

# 需要跳过的帧：logging 内部实现 + 本文件自己
_SKIPPED_FILES = {logging.__file__, __file__}


class InterceptHandler(logging.Handler):
    """把 logging 记录转发给 loguru，保留调用位置与异常信息。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 从 emit 自身的帧开始逐层向上跳过 logging 内部帧，落到真正调用日志的代码，
        # 这样日志前缀里显示的就是真实模块名（实测调用链为 emit → handle → callHandlers
        # → handle → _log → info → 业务代码，共 6 层）。
        frame, depth = logging.currentframe(), 0
        while frame is not None and frame.f_code.co_filename in _SKIPPED_FILES:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: int = logging.INFO) -> None:
    """在 nonebot.init() 之后调用一次。"""
    logging.basicConfig(handlers=[InterceptHandler()], level=level, force=True)

    # NoneBot 已经给 uvicorn 配了 LoguruHandler（见 nonebot/drivers/fastapi.py），
    # 若这些 logger 继续向 root 传播（=我们的拦截器），同一行会在日志里出现两遍。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        logging.getLogger(name).propagate = False

    # 这几个库的 INFO 比较吵，压到 WARNING
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
