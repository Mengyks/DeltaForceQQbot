"""本地表情包索引：读取 manifest 分类，按分类随机挑一张图。

manifest 里的分类名可能带零宽字符（如 ``‌_kg``），实际目录名是 ``_kg``；
统一清洗后再匹配，避免分类找不到。
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ZERO_WIDTH = {ord(ch): None for ch in "\u200b\u200c\u200d\ufeff"}
_PAREN = re.compile(r"[（(].*?[)）]")


def clean_name(name: str) -> str:
    return (name or "").translate(_ZERO_WIDTH).strip()


def canonical_name(name: str) -> str:
    """去掉零宽字符和目录名里的括号说明，得到稳定的工具分类名。"""
    return _PAREN.sub("", clean_name(name)).strip()


def _description(descriptions: dict[str, str], name: str) -> str:
    base = name.strip("_")
    return (
        descriptions.get(name)
        or descriptions.get(base)
        or descriptions.get(f"_{base}")
        or ""
    )


@dataclass(frozen=True)
class MemeCategory:
    name: str
    description: str
    files: tuple[Path, ...]


@dataclass(frozen=True)
class MemeSelection:
    category: str
    path: Path
    data: bytes


class MemeLibrary:
    def __init__(self, root: Path | str, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        self.root = Path(root)
        self.max_bytes = max(int(max_bytes), 1024)
        self._categories: dict[str, MemeCategory] = {}
        self._load()

    def _load(self) -> None:
        if not self.root.exists():
            logger.warning("本地表情包目录不存在：%s（send_meme 工具不可用）", self.root)
            return

        descriptions: dict[str, str] = {}
        manifest = self.root / "manifest.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8-sig"))
                for name, info in (data.get("categories") or {}).items():
                    descriptions[canonical_name(name)] = str((info or {}).get("description") or "")
            except (OSError, ValueError, AttributeError) as exc:
                logger.warning("读取表情包 manifest 失败：%s", exc)

        memes_root = self.root / "memes"
        if not memes_root.is_dir():
            logger.warning("表情包 memes 目录不存在：%s", memes_root)
            return

        for directory in sorted(memes_root.iterdir()):
            if not directory.is_dir():
                continue
            files = tuple(
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file()
                and path.suffix.lower() in IMAGE_SUFFIXES
                and path.stat().st_size <= self.max_bytes
            )
            if not files:
                continue
            name = canonical_name(directory.name)
            if not name:
                continue
            self._categories[name] = MemeCategory(
                name=name,
                description=_description(descriptions, name),
                files=files,
            )

        logger.info(
            "表情包索引完成：%s 个分类、%s 张图（%s）",
            len(self._categories),
            sum(len(category.files) for category in self._categories.values()),
            self.root,
        )

    @property
    def available(self) -> bool:
        return bool(self._categories)

    @property
    def categories(self) -> list[MemeCategory]:
        return [self._categories[name] for name in sorted(self._categories)]

    def category_help(self) -> str:
        return "\n".join(
            f"- {category.name}: {category.description or '（无说明）'}"
            for category in self.categories
        )

    def tool_spec(self) -> dict:
        names = [category.name for category in self.categories]
        return {
            "type": "function",
            "function": {
                "name": "send_meme",
                "description": (
                    "从本地表情包库发一张符合当前聊天场景的图。"
                    "只在确实适合时调用，一次回复最多一张；不要每条消息都发。"
                    "category 必须是给定分类之一。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": names,
                            "description": "表情包分类；含义见系统提示里的分类说明。",
                        }
                    },
                    "required": ["category"],
                },
            },
        }

    def pick(self, category: str, *, avoid: set[str] | None = None) -> MemeSelection | None:
        item = self._categories.get(canonical_name(category))
        if item is None:
            return None
        avoid = avoid or set()
        candidates = [path for path in item.files if str(path) not in avoid]
        if not candidates:
            candidates = list(item.files)
        random.shuffle(candidates)
        for path in candidates:
            try:
                if path.stat().st_size > self.max_bytes:
                    continue
                data = path.read_bytes()
            except OSError as exc:
                logger.debug("读取表情包失败：%s: %s", path, exc)
                continue
            if data:
                return MemeSelection(category=item.name, path=path, data=data)
        return None
