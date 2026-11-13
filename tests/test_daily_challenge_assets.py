"""每日天尊挑战图库：文件名解析与随机选择。"""

from __future__ import annotations

import random
from pathlib import Path

from deltaforce_bot.adapters.daily_challenge_assets import (
    ChallengeAssetCatalog,
)


def make_file(path: Path, size: int = 16) -> None:
    path.write_bytes(b"x" * size)


def test_scan_parses_names_and_ignores_invalid_files(tmp_path: Path) -> None:
    root = tmp_path / "monsters"
    root.mkdir()
    make_file(root / "1、大贼龙.png")
    make_file(root / "27、灭尽龙.png")
    make_file(root / "10、浮空龙.png")
    make_file(root / "readme.txt")
    make_file(root / "没有序号.png")
    (root / "空文件.png").write_bytes(b"")

    catalog = ChallengeAssetCatalog(root, label="怪物")

    assert catalog.available is True
    assert catalog.count == 3
    assert {asset.name for asset in catalog.assets} == {"大贼龙", "灭尽龙", "浮空龙"}
    assert all(asset.path.is_absolute() for asset in catalog.assets)


def test_missing_directory_is_unavailable(tmp_path: Path) -> None:
    catalog = ChallengeAssetCatalog(tmp_path / "missing", label="武器")

    assert catalog.available is False
    assert catalog.count == 0
    assert catalog.pick() is None


def test_pick_returns_one_of_the_assets(tmp_path: Path) -> None:
    root = tmp_path / "weapons"
    root.mkdir()
    make_file(root / "1、大剑.png")
    make_file(root / "2、太刀.png")

    catalog = ChallengeAssetCatalog(root, label="武器")
    picked = {catalog.pick(random.Random(index)).name for index in range(20)}

    assert picked <= {"大剑", "太刀"}
    assert picked, "随机选择不应总是空"
