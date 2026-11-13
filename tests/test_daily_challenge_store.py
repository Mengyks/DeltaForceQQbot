"""每日天尊挑战 SQLite 缓存。"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltaforce_bot.adapters.daily_challenge_store import DailyChallengeStore

DAY = "2026-11-13"
GROUP = 30000
USER = 20000


@pytest.fixture
def store(tmp_path: Path) -> DailyChallengeStore:
    instance = DailyChallengeStore(tmp_path / "daily_challenge.sqlite3")
    yield instance
    instance.close()


def test_save_and_get(store: DailyChallengeStore) -> None:
    store.save(DAY, GROUP, USER, "灭尽龙", "大剑", b"image")

    result = store.get(DAY, GROUP, USER)

    assert result is not None
    assert result.monster_name == "灭尽龙"
    assert result.weapon_name == "大剑"
    assert result.image == b"image"
    assert store.get(DAY, GROUP, USER + 1) is None
    assert store.get(DAY, GROUP + 1, USER) is None
    assert store.get("2026-11-14", GROUP, USER) is None


def test_save_replaces_same_key(store: DailyChallengeStore) -> None:
    store.save(DAY, GROUP, USER, "灭尽龙", "大剑", b"first")
    store.save(DAY, GROUP, USER, "火龙", "太刀", b"second")

    result = store.get(DAY, GROUP, USER)

    assert result is not None
    assert result.monster_name == "火龙"
    assert result.weapon_name == "太刀"
    assert result.image == b"second"


def test_prune_before_keeps_today(store: DailyChallengeStore) -> None:
    store.save("2026-11-11", GROUP, USER, "旧", "旧", b"old")
    store.save("2026-11-12", GROUP, USER, "昨天", "昨天", b"yesterday")
    store.save(DAY, GROUP, USER, "今天", "今天", b"today")

    removed = store.prune_before(DAY)

    assert removed == 2
    assert store.get("2026-11-11", GROUP, USER) is None
    assert store.get("2026-11-12", GROUP, USER) is None
    assert store.get(DAY, GROUP, USER) is not None
