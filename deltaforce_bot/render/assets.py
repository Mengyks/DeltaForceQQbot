"""图片资源查找：把接口返回的装备映射到 assets 目录下的图片文件。

目录约定（用户整理）：

    assets/items/
    ├── armor/Level{1..6}/<装备名>.png        护甲
    ├── helmets/Level{1..6}/<装备名>.png      头盔
    ├── backpacks/Level{1..6}/<装备名>.png    背包
    ├── chest_rigs/Level{1..5}/<装备名>.png   胸挂（无 6 级）
    ├── weapon/<装备名>.png                   枪械（配件不用图片）
    └── backgrounds/<地图名>.png              卡片背景

查表规则（用户确认）：
- 护甲/头盔/背包/胸挂 → 按 `grade` 进对应 Level 目录，找不到再在同部位的其他 Level 里兜底；
- 枪1/枪2/手枪 → weapon；
- 配件（type 形如「枪1-瞄具」）→ 不用图片，卡片上用文字；
- 名称归一化：去掉耐久度后缀（(破损)/(几乎全新)/(全新)）与所有空格、忽略大小写。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_CONDITION = re.compile(r"\s*[（(](破损|几乎全新|全新)[)）]\s*")
_WHITESPACE = re.compile(r"\s+")
_LEVEL_DIR = re.compile(r"level(\d)", flags=re.IGNORECASE)

SLOT_FOLDERS = {"护甲": "armor", "头盔": "helmets", "背包": "backpacks", "胸挂": "chest_rigs"}
GUN_TYPES = {"枪1", "枪2", "手枪"}


def normalize_name(name: str) -> str:
    """归一化装备名：去耐久度后缀、去空格、统一括号、小写。"""
    text = _CONDITION.sub("", (name or "").strip())
    text = text.replace("（", "(").replace("）", ")")
    return _WHITESPACE.sub("", text).lower()


def parse_condition(name: str) -> str:
    """从装备名里解析耐久程度；没有后缀视为「全新」。"""
    match = _CONDITION.search(name or "")
    return match.group(1) if match else "全新"


def is_attachment(item_type: str | None) -> bool:
    """形如「枪1-瞄具」的部位是配件（用文字呈现，不查图）。"""
    return bool(item_type) and "-" in item_type


class AssetLibrary:
    """资源目录索引：启动时扫描一次，之后全部走内存查表。

    索引键是 (相对目录, 归一化文件名)，因此优先精确命中「部位/等级」目录，
    也方便做"等级目录里没有、去同部位其他等级兜底"。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._index: dict[tuple[str, str], Path] = {}
        self._backgrounds: dict[str, Path] = {}
        self._scan()

    def _scan(self) -> None:
        if not self.root.exists():
            logger.warning("资源目录不存在：%s（图片卡片将全部走占位块）", self.root)
            return

        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            rel_dir = path.parent.relative_to(self.root).as_posix().lower()
            if rel_dir == "backgrounds":
                self._backgrounds[normalize_name(path.stem)] = path
                continue
            key = (rel_dir, normalize_name(path.stem))
            self._index.setdefault(key, path)

        logger.info(
            "资源索引完成：装备图 %s 张、背景图 %s 张（目录 %s）",
            len(self._index), len(self._backgrounds), self.root,
        )

    def item_image(self, name: str, item_type: str | None, grade: int | None) -> Path | None:
        """查找装备图片；配件与找不到的返回 None（模板会渲染占位块/文字）。"""
        if is_attachment(item_type):
            return None

        key_name = normalize_name(name)
        folder = SLOT_FOLDERS.get(item_type or "")

        if folder:
            levels = [grade] if grade else []
            levels += [n for n in range(1, 7) if n not in levels]  # 等级里没有就同部位兜底
            for level in levels:
                hit = self._index.get((f"{folder}/level{level}", key_name))
                if hit:
                    return hit
            return None

        if item_type in GUN_TYPES:
            return self._index.get(("weapon", key_name))

        # 兜底：任何目录里同名文件
        for (rel_dir, stem), path in self._index.items():
            if stem == key_name:
                return path
        return None

    def background(self, map_name: str) -> Path | None:
        return self._backgrounds.get(normalize_name(map_name))

    def levels_of(self, item_type: str) -> list[int]:
        """该部位实际存在的等级目录（用于自检/测试）。"""
        folder = SLOT_FOLDERS.get(item_type)
        if not folder:
            return []
        levels = []
        for rel_dir, _ in self._index:
            if rel_dir.startswith(f"{folder}/level"):
                match = _LEVEL_DIR.search(rel_dir)
                if match:
                    levels.append(int(match.group(1)))
        return sorted(set(levels))

    @property
    def stats(self) -> tuple[int, int]:
        return len(self._index), len(self._backgrounds)
