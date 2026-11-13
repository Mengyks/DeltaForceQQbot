"""猪猪表情包：命中固定触发词时，从本地目录随机挑一张图。

与聊天表情包库（``adapters/memes.py``）解耦：不读 manifest、不依赖 LLM，
目录在启动时扫描，新增图片需要重启进程。

触发是**按群隔离**的：发送只在原群回复，冷却也按 group_id 分开记录，
A 群触发不会影响 B 群，也不会向其他群广播。
"""

from __future__ import annotations

import logging
import random
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PIG_TRIGGERS = frozenset({"猪", "zhu", "pig", "🐖", "🐷"})
_ZERO_WIDTH = {ord(ch): None for ch in "\u200b\u200c\u200d\ufeff"}


def normalize_trigger(text: str) -> str:
    """统一全角/大小写/首尾空白，并去掉零宽字符。"""
    return (
        unicodedata.normalize("NFKC", text or "")
        .translate(_ZERO_WIDTH)
        .strip()
        .casefold()
    )


def is_pig_trigger(text: str) -> bool:
    """Text 是否**恰好**是触发词之一；「猪猪」「小猪」「猪！」都不算。"""
    return normalize_trigger(text) in PIG_TRIGGERS


@dataclass(frozen=True)
class PigMeme:
    path: Path
    data: bytes


class PigMemeService:
    def __init__(
        self,
        directory: Path | str,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        cooldown: float = 5.0,
        recent_exclude: int = 8,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory = Path(directory)
        self.max_bytes = max(int(max_bytes), 1024)
        self.recent_exclude = max(int(recent_exclude), 0)
        self._files: tuple[Path, ...] = ()
        self._recent: deque[str] = deque(maxlen=max(self.recent_exclude, 1))
        self._limiter = (
            SlidingWindowLimiter(1, float(cooldown), time_fn) if cooldown > 0 else None
        )
        self._load()

    def _load(self) -> None:
        if not self.directory.exists():
            logger.warning("猪猪表情包目录不存在：%s（功能不可用）", self.directory)
            return
        if not self.directory.is_dir():
            logger.warning("猪猪表情包路径不是目录：%s（功能不可用）", self.directory)
            return

        files: list[Path] = []
        for path in sorted(self.directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                logger.debug("读取猪猪表情包信息失败：%s: %s", path, exc)
                continue
            if size <= 0 or size > self.max_bytes:
                continue
            files.append(path)

        self._files = tuple(files)
        logger.info(
            "猪猪表情包索引完成：%s 张（%s）",
            len(self._files),
            self.directory,
        )

    @property
    def available(self) -> bool:
        return bool(self._files)

    @property
    def count(self) -> int:
        return len(self._files)

    def allow(self, group_id: int) -> bool:
        """按群占用一次触发额度；冷却内返回 False，不影响其他群。"""
        if self._limiter is None:
            return True
        return self._limiter.allow(str(group_id))[0]

    def pick(self) -> PigMeme | None:
        if not self._files:
            return None

        candidates = list(self._files)
        if self.recent_exclude:
            candidates = [path for path in candidates if str(path) not in self._recent]
            if not candidates:
                candidates = list(self._files)
        random.shuffle(candidates)

        for path in candidates:
            try:
                data = path.read_bytes()
            except OSError as exc:
                logger.debug("读取猪猪表情包失败：%s: %s", path, exc)
                continue
            if not data or len(data) > self.max_bytes:
                continue
            if self.recent_exclude:
                self._recent.append(str(path))
            return PigMeme(path=path, data=data)
        return None
