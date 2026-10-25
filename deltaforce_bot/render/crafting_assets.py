"""制造台物品图片索引：按台 + 等级目录找图，名称使用宽松归一化。

API 的物品名和文件名存在一些无法直接作为 Windows 文件名的差异：
- 前导点：``.300BLK_3`` vs ``300BLK_3``
- 连字符/空格：``MCX LT`` vs ``MCX-LT``
- 斜杠/下划线：``3/7`` vs ``3_7``
- 引号：``9号霰射"鼠弹"``
- 末尾下划线：``45 ACP Super`` vs ``45 ACP Super_``

因此查找时先做 NFKC + 小写 + 只保留字母/数字/中文，再匹配；
目录按制造台规则定位，等级目录大小写不敏感。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from deltaforce_bot.models.crafting import CraftingItem

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
CRAFTING_FOLDERS = (
    "supplies",
    "accessories",
    "weapon",
    "helmets",
    "backpacks",
    "armor",
    "chest_rigs",
    "bullet",
)
ARMOR_SLOTS = {
    "helmet": "helmets",
    "bag": "backpacks",
    "armor": "armor",
    "chest": "chest_rigs",
}
_NON_NAME = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def canonical_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or "")).lower()
    return _NON_NAME.sub("", text)


@dataclass(frozen=True)
class CraftingImage:
    path: Path
    level: str


class CraftingAssetLibrary:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # 文件夹（小写）→ canonical 名称 → 图片列表
        self._index: dict[str, dict[str, list[CraftingImage]]] = {
            folder: {} for folder in CRAFTING_FOLDERS
        }
        self._scan()

    def _scan(self) -> None:
        if not self.root.exists():
            logger.warning("制造台资源目录不存在：%s（物品图将使用占位块）", self.root)
            return

        total = 0
        for folder in CRAFTING_FOLDERS:
            directory = self.root / folder
            if not directory.is_dir():
                logger.warning("制造台图片目录缺失：%s", directory)
                continue
            index = self._index[folder]
            for path in directory.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                key = canonical_name(path.stem)
                if not key:
                    continue
                index.setdefault(key, []).append(
                    CraftingImage(path=path, level=path.parent.name.lower())
                )
                total += 1
        logger.info(
            "制造台图片索引完成：%s 张（目录 %s）",
            total,
            self.root,
        )

    def find(self, station_id: int, item: CraftingItem) -> Path | None:
        """按制造台规则找图；找不到返回 None，由模板渲染占位块。"""
        key = canonical_name(item.name)
        if not key:
            return None

        folder, level = self._target(station_id, item)
        if folder is None:
            # 防具台分类异常时，兜底在四个防具目录里按名称找
            for fallback in ("helmets", "backpacks", "armor", "chest_rigs"):
                hit = self._pick(fallback, key, level)
                if hit is not None:
                    return hit.path
            return None
        hit = self._pick(folder, key, level)
        return hit.path if hit is not None else None

    def _target(self, station_id: int, item: CraftingItem) -> tuple[str | None, str | None]:
        if station_id == 1:
            if item.primary_class == "acc":
                return "accessories", f"level{item.grade}"
            return "weapon", None
        if station_id == 2:
            return "bullet", f"level{item.grade}"
        if station_id == 3:
            return "supplies", f"level{item.grade}"
        if station_id == 4:
            folder = ARMOR_SLOTS.get(item.second_class)
            if folder is None:
                return None, f"level{item.grade}"
            return folder, f"level{item.grade}"
        return None, None

    def _pick(self, folder: str, key: str, level: str | None) -> CraftingImage | None:
        candidates = self._index.get(folder, {}).get(key, [])
        if not candidates:
            return None
        if level is None:
            return candidates[0]
        expected = level.lower()
        for candidate in candidates:
            if candidate.level == expected:
                return candidate
        # 等级目录放错/大小写不一致时，仍然给一张图，避免整格空白
        logger.debug("制造台图片等级不匹配：%s 期望 %s，实际 %s", key, expected, [c.level for c in candidates])
        return candidates[0]

    @property
    def stats(self) -> dict[str, int]:
        return {folder: sum(len(items) for items in index.values()) for folder, index in self._index.items()}
