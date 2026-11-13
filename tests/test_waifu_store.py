"""猪结婚 SQLite 配对存储：双向查询、唯一约束、清理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltaforce_bot.adapters.waifu_store import WaifuPairConflict, WaifuStore

DAY = "2026-11-13"
GROUP = 30000


@pytest.fixture
def store(tmp_path: Path) -> WaifuStore:
    instance = WaifuStore(tmp_path / "waifu.sqlite3")
    yield instance
    instance.close()


def test_pair_is_visible_from_both_sides(store: WaifuStore) -> None:
    store.create_pair(DAY, GROUP, 1, "群友A", 2, "群友B", b"image-bytes")

    result_a = store.get_partner(DAY, GROUP, 1)
    result_b = store.get_partner(DAY, GROUP, 2)

    assert result_a is not None
    assert result_a.partner_id == 2
    assert result_a.partner_name == "群友B"
    assert result_a.image == b"image-bytes"

    assert result_b is not None
    assert result_b.partner_id == 1
    assert result_b.partner_name == "群友A"
    assert result_b.image == b"image-bytes"


def test_paired_users_and_count(store: WaifuStore) -> None:
    store.create_pair(DAY, GROUP, 1, "A", 2, "B", b"1")
    store.create_pair(DAY, GROUP, 3, "C", 4, "D", b"2")

    assert store.paired_users(DAY, GROUP) == {1, 2, 3, 4}
    assert store.pair_count(DAY, GROUP) == 2


def test_day_and_group_are_isolated(store: WaifuStore) -> None:
    store.create_pair(DAY, GROUP, 1, "A", 2, "B", b"1")

    assert store.get_partner("2026-11-14", GROUP, 1) is None
    assert store.get_partner(DAY, GROUP + 1, 1) is None


def test_same_user_cannot_be_in_two_pairs(store: WaifuStore) -> None:
    store.create_pair(DAY, GROUP, 1, "A", 2, "B", b"1")

    with pytest.raises(WaifuPairConflict):
        store.create_pair(DAY, GROUP, 3, "C", 2, "B", b"2")
    with pytest.raises(WaifuPairConflict):
        store.create_pair(DAY, GROUP, 1, "A", 4, "D", b"3")

    # 冲突要整体回滚：不能留下半条配对或空的图片行
    assert store.pair_count(DAY, GROUP) == 1
    assert store.paired_users(DAY, GROUP) == {1, 2}
    assert store.get_partner(DAY, GROUP, 3) is None
    assert store.get_partner(DAY, GROUP, 4) is None


def test_prune_before_keeps_today(store: WaifuStore) -> None:
    store.create_pair("2026-11-11", GROUP, 1, "A", 2, "B", b"old")
    store.create_pair("2026-11-12", GROUP, 3, "C", 4, "D", b"yesterday")
    store.create_pair(DAY, GROUP, 5, "E", 6, "F", b"today")

    removed = store.prune_before(DAY)

    assert removed == 2
    assert store.get_partner("2026-11-11", GROUP, 1) is None
    assert store.get_partner("2026-11-12", GROUP, 3) is None
    assert store.get_partner(DAY, GROUP, 5) is not None
