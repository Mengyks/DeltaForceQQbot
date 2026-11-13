"""猪结婚服务：配对、共享缓存、排除已配对成员、头像失败重试。"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pytest
from PIL import Image

from deltaforce_bot.adapters.waifu_avatar import WaifuAvatarError
from deltaforce_bot.adapters.waifu_store import WaifuStore
from deltaforce_bot.models.waifu import WaifuMember
from deltaforce_bot.render.waifu_card import TEMPLATE_SIZE
from deltaforce_bot.services.waifu import (
    WaifuAvatarDownloadError,
    WaifuNoCandidates,
    WaifuService,
    WaifuUnavailable,
)

DAY = "2026-11-13"
GROUP = 30000


def avatar_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 640), color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class FakeAvatarClient:
    def __init__(
        self,
        *,
        fail_ids: set[int] | None = None,
        fail_first_target_calls: int = 0,
    ) -> None:
        self.fail_ids = fail_ids or set()
        self.fail_first_target_calls = fail_first_target_calls
        self._target_calls = 0
        self.calls: list[int] = []

    async def fetch(self, user_id: int) -> bytes:
        self.calls.append(user_id)
        # 第一次调用是发送者头像；之后才是候选目标
        if len(self.calls) > 1 and self._target_calls < self.fail_first_target_calls:
            self._target_calls += 1
            raise WaifuAvatarError(f"fake first-target failure {user_id}")
        if user_id in self.fail_ids:
            raise WaifuAvatarError(f"fake failure {user_id}")
        return avatar_bytes((user_id * 29 % 255, 70, 150))


def members(*pairs) -> list[WaifuMember]:
    return [WaifuMember(user_id, name, robot) for user_id, name, robot in pairs]


@pytest.fixture
def store(tmp_path: Path) -> WaifuStore:
    instance = WaifuStore(tmp_path / "waifu.sqlite3")
    yield instance
    instance.close()


def make_service(
    store: WaifuStore,
    avatar: FakeAvatarClient | None = None,
    *,
    max_attempts: int = 3,
    today: list[str] | None = None,
    seed: int = 0,
) -> WaifuService:
    template = Image.new("RGB", TEMPLATE_SIZE, (255, 255, 255))
    return WaifuService(
        store,
        avatar or FakeAvatarClient(),
        template,
        max_attempts=max_attempts,
        today_fn=(lambda: (today or [DAY])[0]),
        rng=random.Random(seed),
    )


async def test_first_sender_forms_pair_and_returns_image(store: WaifuStore) -> None:
    service = make_service(store)
    people = members((2, "群友B", False), (3, "群友C", False), (4, "群友D", False))

    result = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )

    assert result.partner_id in {2, 3, 4}
    assert result.image
    assert store.paired_users(DAY, GROUP) == {1, result.partner_id}
    assert store.pair_count(DAY, GROUP) == 1

    sender_side = store.get_partner(DAY, GROUP, 1)
    partner_side = store.get_partner(DAY, GROUP, result.partner_id)
    assert sender_side is not None and sender_side.partner_id == result.partner_id
    assert partner_side is not None and partner_side.partner_id == 1
    assert sender_side.image == result.image


async def test_same_sender_hits_cache_without_avatar_fetch(store: WaifuStore) -> None:
    avatar = FakeAvatarClient()
    service = make_service(store, avatar)
    people = members((2, "群友B", False), (3, "群友C", False))

    first = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )
    avatar.calls.clear()
    second = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )

    assert second == first
    assert avatar.calls == []


async def test_partner_shares_pair_and_image(store: WaifuStore) -> None:
    service = make_service(store)
    people = members((2, "群友B", False), (3, "群友C", False))

    first = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )
    partner = await service.handle(
        group_id=GROUP,
        sender_id=first.partner_id,
        sender_name="群友B",
        members=people,
    )

    assert partner.partner_id == 1
    assert partner.partner_name == "群友A"
    assert partner.image == first.image


async def test_other_members_cannot_select_a_pair(store: WaifuStore) -> None:
    service = make_service(store)
    people = members((2, "群友B", False), (3, "群友C", False), (4, "群友D", False))

    first = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )
    paired = {1, first.partner_id}
    remaining = [member for member in people if member.user_id not in paired]
    assert remaining, "测试数据应至少剩下一个未配对成员"

    other = remaining[0]
    second = await service.handle(
        group_id=GROUP,
        sender_id=other.user_id,
        sender_name=other.name,
        members=people,
    )

    assert second.partner_id not in paired
    assert second.partner_id != other.user_id
    assert store.paired_users(DAY, GROUP) == paired | {
        other.user_id,
        second.partner_id,
    }


async def test_no_candidates_when_only_sender_and_robots(store: WaifuStore) -> None:
    service = make_service(store)
    people = members((1, "群友A", False), (999, "机器人", True))

    with pytest.raises(WaifuNoCandidates):
        await service.handle(
            group_id=GROUP, sender_id=1, sender_name="群友A", members=people
        )
    assert store.paired_users(DAY, GROUP) == set()


async def test_avatar_failure_retries_another_candidate(store: WaifuStore) -> None:
    avatar = FakeAvatarClient(fail_first_target_calls=1)
    service = make_service(store, avatar, max_attempts=3)
    people = members((2, "群友B", False), (3, "群友C", False), (4, "群友D", False))

    result = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )

    # 1 次发送者 + 至少 2 次候选（第一次候选失败后换人）
    assert len(avatar.calls) >= 3
    assert store.paired_users(DAY, GROUP) == {1, result.partner_id}


async def test_all_target_avatars_fail_creates_no_pair(store: WaifuStore) -> None:
    avatar = FakeAvatarClient(fail_ids={2, 3, 4})
    service = make_service(store, avatar, max_attempts=3)
    people = members((2, "群友B", False), (3, "群友C", False), (4, "群友D", False))

    with pytest.raises(WaifuAvatarDownloadError):
        await service.handle(
            group_id=GROUP, sender_id=1, sender_name="群友A", members=people
        )
    assert store.paired_users(DAY, GROUP) == set()


async def test_sender_avatar_failure_creates_no_pair(store: WaifuStore) -> None:
    avatar = FakeAvatarClient(fail_ids={1})
    service = make_service(store, avatar)
    people = members((2, "群友B", False), (3, "群友C", False))

    with pytest.raises(WaifuAvatarDownloadError):
        await service.handle(
            group_id=GROUP, sender_id=1, sender_name="群友A", members=people
        )
    assert store.paired_users(DAY, GROUP) == set()


async def test_day_boundary_resets_pairing(store: WaifuStore) -> None:
    today = [DAY]
    service = make_service(store, today=today, seed=1)
    people = members((2, "群友B", False), (3, "群友C", False))

    first = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )
    today[0] = "2026-11-14"
    second = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )

    assert store.pair_count(DAY, GROUP) == 1
    assert store.pair_count("2026-11-14", GROUP) == 1
    assert store.paired_users(DAY, GROUP) == {1, first.partner_id}
    assert store.paired_users("2026-11-14", GROUP) == {1, second.partner_id}


async def test_old_days_are_pruned_on_first_use(store: WaifuStore) -> None:
    store.create_pair("2026-11-10", GROUP, 7, "老A", 8, "老B", b"old")
    service = make_service(store, seed=3)

    await service.handle(
        group_id=GROUP,
        sender_id=1,
        sender_name="群友A",
        members=members((2, "群友B", False)),
    )

    assert store.pair_count("2026-11-10", GROUP) == 0


async def test_group_isolation(store: WaifuStore) -> None:
    service = make_service(store, seed=2)
    people = members((2, "群友B", False), (3, "群友C", False))

    first = await service.handle(
        group_id=GROUP, sender_id=1, sender_name="群友A", members=people
    )
    second = await service.handle(
        group_id=GROUP + 1, sender_id=1, sender_name="群友A", members=people
    )

    assert store.pair_count(DAY, GROUP) == 1
    assert store.pair_count(DAY, GROUP + 1) == 1
    assert store.paired_users(DAY, GROUP) == {1, first.partner_id}
    assert store.paired_users(DAY, GROUP + 1) == {1, second.partner_id}


async def test_unavailable_without_template(store: WaifuStore) -> None:
    service = WaifuService(store, FakeAvatarClient(), None)

    assert service.available is False
    with pytest.raises(WaifuUnavailable):
        await service.handle(
            group_id=GROUP, sender_id=1, sender_name="A", members=[]
        )
