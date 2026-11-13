"""每日天尊挑战服务：随机素材、按群/人/天缓存、头像与渲染失败。"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from deltaforce_bot.adapters.daily_challenge_assets import ChallengeAssetCatalog
from deltaforce_bot.adapters.daily_challenge_store import DailyChallengeStore
from deltaforce_bot.adapters.waifu_avatar import WaifuAvatarError
from deltaforce_bot.services.daily_challenge import (
    DailyChallengeAvatarError,
    DailyChallengeRenderError,
    DailyChallengeService,
    DailyChallengeUnavailable,
)
from deltaforce_bot.render.renderer import RenderError

DAY = "2026-11-13"
GROUP = 30000
USER = 20000
_DEFAULT = object()


class FakeAvatarClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[int] = []

    async def fetch(self, user_id: int) -> bytes:
        self.calls.append(user_id)
        if self.fail:
            raise WaifuAvatarError("fake failure")
        return b"\xff\xd8\xffavatar"


class StubRenderer:
    def __init__(self, *, error: Exception | None = None, payload: bytes = b"image") -> None:
        self.error = error
        self.payload = payload
        self.calls: list[object] = []

    async def render_template(self, context, **kwargs):
        self.calls.append(context)
        if self.error is not None:
            raise self.error
        return self.payload


def make_files(root: Path, names: list[str], *, suffix: str = ".png") -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(b"fake")


@pytest.fixture
def store(tmp_path: Path) -> DailyChallengeStore:
    instance = DailyChallengeStore(tmp_path / "daily_challenge.sqlite3")
    yield instance
    instance.close()


def make_service(
    tmp_path: Path,
    store: DailyChallengeStore,
    *,
    avatar: FakeAvatarClient | None = None,
    renderer: StubRenderer | None | object = _DEFAULT,
    today: list[str] | None = None,
    seed: int = 0,
    with_monsters: bool = True,
    with_weapons: bool = True,
) -> DailyChallengeService:
    monster_dir = tmp_path / "monsters"
    weapon_dir = tmp_path / "weapons"
    if with_monsters:
        make_files(monster_dir, ["1、怪物A.png", "2、怪物B.png"])
    if with_weapons:
        make_files(weapon_dir, ["1、武器A.png", "2、武器B.png"])
    background = tmp_path / "background.jpeg"
    background.write_bytes(b"fake-jpeg")

    return DailyChallengeService(
        store,
        avatar or FakeAvatarClient(),
        ChallengeAssetCatalog(monster_dir, label="怪物"),
        ChallengeAssetCatalog(weapon_dir, label="武器"),
        background,
        StubRenderer() if renderer is _DEFAULT else renderer,
        today_fn=lambda: (today or [DAY])[0],
        rng=random.Random(seed),
    )


async def test_first_call_picks_assets_and_writes_cache(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    avatar = FakeAvatarClient()
    renderer = StubRenderer(payload=b"challenge-image")
    service = make_service(tmp_path, store, avatar=avatar, renderer=renderer)

    result = await service.handle(
        group_id=GROUP, user_id=USER, hunter_name="群友A"
    )

    assert result.monster_name in {"怪物A", "怪物B"}
    assert result.weapon_name in {"武器A", "武器B"}
    assert result.image == b"challenge-image"
    assert avatar.calls == [USER]
    assert len(renderer.calls) == 1
    assert renderer.calls[0].hunter_name == "群友A"

    cached = store.get(DAY, GROUP, USER)
    assert cached is not None
    assert cached.monster_name == result.monster_name
    assert cached.weapon_name == result.weapon_name
    assert cached.image == result.image


async def test_same_user_group_day_hits_cache(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    avatar = FakeAvatarClient()
    renderer = StubRenderer()
    service = make_service(tmp_path, store, avatar=avatar, renderer=renderer)

    first = await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")
    avatar.calls.clear()
    renderer.calls.clear()
    second = await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")

    assert second == first
    assert avatar.calls == []
    assert renderer.calls == []


async def test_different_user_and_group_are_separate(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    avatar = FakeAvatarClient()
    service = make_service(tmp_path, store, avatar=avatar, renderer=StubRenderer())

    first = await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")
    second = await service.handle(group_id=GROUP, user_id=USER + 1, hunter_name="B")
    third = await service.handle(group_id=GROUP + 1, user_id=USER, hunter_name="A")

    assert store.get(DAY, GROUP, USER) is not None
    assert store.get(DAY, GROUP, USER + 1) is not None
    assert store.get(DAY, GROUP + 1, USER) is not None
    assert avatar.calls == [USER, USER + 1, USER]
    # 随机组合可能相同，但三份缓存必须是独立记录
    assert first.image == second.image == third.image


async def test_day_boundary_generates_new_cache(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    today = [DAY]
    service = make_service(tmp_path, store, today=today)

    first = await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")
    today[0] = "2026-11-14"
    second = await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")

    assert store.get(DAY, GROUP, USER) is not None
    assert store.get("2026-11-14", GROUP, USER) is not None
    assert first.image == second.image


async def test_avatar_failure_creates_no_cache(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    service = make_service(
        tmp_path, store, avatar=FakeAvatarClient(fail=True), renderer=StubRenderer()
    )

    with pytest.raises(DailyChallengeAvatarError):
        await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")
    assert store.get(DAY, GROUP, USER) is None


async def test_render_failure_creates_no_cache(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    service = make_service(
        tmp_path,
        store,
        renderer=StubRenderer(error=RenderError("boom")),
    )

    with pytest.raises(DailyChallengeRenderError):
        await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")
    assert store.get(DAY, GROUP, USER) is None


async def test_missing_catalog_is_unavailable(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    service = make_service(tmp_path, store, with_monsters=False)

    assert service.available is False
    with pytest.raises(DailyChallengeUnavailable):
        await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")


async def test_none_renderer_is_unavailable(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    service = make_service(tmp_path, store, renderer=None)

    assert service.available is False


async def test_old_cache_pruned_on_first_use(
    tmp_path: Path, store: DailyChallengeStore
) -> None:
    store.save("2026-11-10", GROUP, USER, "旧怪物", "旧武器", b"old")
    service = make_service(tmp_path, store)

    await service.handle(group_id=GROUP, user_id=USER, hunter_name="A")

    assert store.get("2026-11-10", GROUP, USER) is None
