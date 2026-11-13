"""每日天尊挑战的怪物/武器图库索引。

文件名格式为 ``序号、名称``（例如 ``27、灭尽龙.png``），
索引时只保留“、”后面的名称；不符合格式的文件跳过并记 warning。
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_SEQUENCE_NAME = re.compile(r"^\d+、\s*(.+)$")


@dataclass(frozen=True)
class ChallengeAsset:
    name: str
    path: Path


class ChallengeAssetCatalog:
    def __init__(self, directory: Path | str, *, label: str = "素材") -> None:
        self.directory = Path(directory).resolve()
        self.label = label
        self._assets: tuple[ChallengeAsset, ...] = ()
        self._scan()

    def _scan(self) -> None:
        if not self.directory.exists() or not self.directory.is_dir():
            logger.warning("%s图库目录不存在：%s（功能不可用）", self.label, self.directory)
            return

        assets: list[ChallengeAsset] = []
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            match = _SEQUENCE_NAME.match(path.stem)
            if match is None:
                logger.warning("%s图库文件名不符合「序号、名称」：%s", self.label, path.name)
                continue
            name = match.group(1).strip()
            if not name:
                continue
            try:
                if path.stat().st_size <= 0:
                    continue
            except OSError as exc:
                logger.debug("读取%s图库文件信息失败：%s: %s", self.label, path, exc)
                continue
            assets.append(ChallengeAsset(name=name, path=path))

        self._assets = tuple(assets)
        logger.info(
            "%s图库索引完成：%s 张（%s）",
            self.label,
            len(self._assets),
            self.directory,
        )

    @property
    def available(self) -> bool:
        return bool(self._assets)

    @property
    def count(self) -> int:
        return len(self._assets)

    @property
    def assets(self) -> tuple[ChallengeAsset, ...]:
        return self._assets

    def pick(self, rng: random.Random | None = None) -> ChallengeAsset | None:
        if not self._assets:
            return None
        return (rng or random).choice(self._assets)
