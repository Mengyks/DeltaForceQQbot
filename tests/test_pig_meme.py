"""猪猪表情包：触发词、目录扫描、随机挑选、按群冷却与插件发送。"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from deltaforce_bot.services.pig_meme import (
    PigMemeService,
    is_pig_trigger,
    normalize_trigger,
)
from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "pig_meme.py"


# ---------- 触发词 ----------
def test_trigger_words_exact_match() -> None:
    assert is_pig_trigger("猪")
    assert is_pig_trigger("zhu")
    assert is_pig_trigger("pig")
    assert is_pig_trigger("🐖")
    assert is_pig_trigger("🐷")


def test_trigger_normalizes_space_case_width_and_zero_width() -> None:
    assert is_pig_trigger("  ZHU  ")
    assert is_pig_trigger("PIG")
    assert is_pig_trigger("ｐｉｇ")  # 全角 → NFKC
    assert is_pig_trigger("\u200b猪\u200b")


def test_trigger_rejects_partial_or_punctuated_text() -> None:
    assert not is_pig_trigger("")
    assert not is_pig_trigger("猪猪")
    assert not is_pig_trigger("小猪")
    assert not is_pig_trigger("猪！")
    assert not is_pig_trigger("piggy")
    assert not is_pig_trigger("zhu zhu")
    assert not is_pig_trigger("🐖🐖")


def test_normalize_trigger_keeps_chinese_and_emoji() -> None:
    assert normalize_trigger(" 猪 ") == "猪"
    assert normalize_trigger("🐷") == "🐷"


# ---------- 服务 ----------
def make_images(tmp_path: Path, count: int = 3, *, size: int = 32) -> Path:
    root = tmp_path / "pig"
    nested = root / "nested"
    nested.mkdir(parents=True)
    for index in range(count):
        target = (nested if index % 2 else root) / f"pig_{index}.png"
        target.write_bytes(f"image-{index}".encode().ljust(size, b"\0"))
    (root / "readme.txt").write_text("not an image", encoding="utf-8")
    return root


def test_scan_indexes_nested_images_and_ignores_other_files(tmp_path: Path) -> None:
    root = make_images(tmp_path, 3)
    service = PigMemeService(root, max_bytes=1024)

    assert service.available is True
    assert service.count == 3


def test_missing_directory_is_unavailable(tmp_path: Path) -> None:
    service = PigMemeService(tmp_path / "missing")

    assert service.available is False
    assert service.count == 0
    assert service.pick() is None


def test_empty_directory_is_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "pig"
    root.mkdir()

    service = PigMemeService(root)

    assert service.available is False
    assert service.pick() is None


def test_oversized_file_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "pig"
    root.mkdir()
    (root / "big.png").write_bytes(b"x" * 2048)

    service = PigMemeService(root, max_bytes=1024)

    assert service.count == 0
    assert service.pick() is None


def test_pick_returns_one_of_the_indexed_files(tmp_path: Path) -> None:
    root = make_images(tmp_path, 3)
    service = PigMemeService(root, max_bytes=1024)

    meme = service.pick()

    assert meme is not None
    assert meme.path.parent.parent == root or meme.path.parent == root
    assert meme.data.startswith(b"image-")


def test_recent_exclude_avoids_immediate_repeat(tmp_path: Path) -> None:
    root = make_images(tmp_path, 2)
    service = PigMemeService(root, max_bytes=1024, recent_exclude=1)

    first = service.pick()
    second = service.pick()

    assert first is not None and second is not None
    assert first.path != second.path


def test_cooldown_is_per_group(tmp_path: Path) -> None:
    root = make_images(tmp_path, 1)
    now = [100.0]
    service = PigMemeService(root, cooldown=5, time_fn=lambda: now[0])

    assert service.allow(111) is True
    assert service.allow(111) is False
    # A 群冷却不影响 B 群
    assert service.allow(222) is True

    now[0] += 5.0
    assert service.allow(111) is True


def test_cooldown_zero_disables_throttling(tmp_path: Path) -> None:
    root = make_images(tmp_path, 1)
    service = PigMemeService(root, cooldown=0)

    assert service.allow(111) is True
    assert service.allow(111) is True


# ---------- 插件 ----------
@pytest.fixture(scope="module")
def plugin():
    loaded = nonebot.get_plugin("pig_meme")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "猪猪表情包插件加载失败"
    return loaded


def make_event(text: str, *, group_id: int = 30000) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=10000,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=20000,
        message=Message(text),
        raw_message=text,
        font=0,
        sender={},
        group_id=group_id,
    )


def test_matcher_priority(plugin) -> None:
    assert plugin.module.pig_meme_matcher.priority == 10
    assert plugin.module.pig_meme_matcher.block is True


def test_plugin_rule_matches_only_exact_triggers(plugin, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin.module,
        "get_runtime",
        lambda: SimpleNamespace(settings=SimpleNamespace(fun_group_set={30000})),
    )
    rule = plugin.module._is_pig_trigger

    assert rule(make_event("猪")) is True
    assert rule(make_event(" zhu ")) is True
    assert rule(make_event("PIG")) is True
    assert rule(make_event("🐖")) is True
    assert rule(make_event("🐷")) is True

    assert rule(make_event("猪猪")) is False
    assert rule(make_event("小猪")) is False
    assert rule(make_event("猪！")) is False
    assert rule(make_event("/卡战备 巴克什 绝密")) is False
    assert rule(make_event("猪", group_id=30001)) is False


class FakeMatcher:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.sent: list[Message] = []

    async def send(self, message: Message) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(message)


async def call_handler(plugin, monkeypatch, event, runtime, *, fail_send: bool = False):
    matcher = FakeMatcher(fail_send=fail_send)
    monkeypatch.setattr(plugin.module, "pig_meme_matcher", matcher)
    monkeypatch.setattr(plugin.module, "get_runtime", lambda: runtime)
    await plugin.module.handle_pig_meme(event)
    return matcher


async def test_handler_sends_image(plugin, monkeypatch, tmp_path: Path) -> None:
    root = make_images(tmp_path, 2)
    runtime = SimpleNamespace(pig_meme=PigMemeService(root, max_bytes=1024))
    matcher = await call_handler(plugin, monkeypatch, make_event("猪"), runtime)

    assert len(matcher.sent) == 1
    segment = matcher.sent[0][0]
    assert segment.type == "image"
    encoded = str(segment.data["file"]).removeprefix("base64://")
    assert base64.b64decode(encoded).startswith(b"image-")


async def test_handler_cooldown_only_affects_same_group(
    plugin, monkeypatch, tmp_path: Path
) -> None:
    root = make_images(tmp_path, 2)
    runtime = SimpleNamespace(pig_meme=PigMemeService(root, max_bytes=1024, cooldown=5))

    first = await call_handler(plugin, monkeypatch, make_event("猪", group_id=30000), runtime)
    second = await call_handler(plugin, monkeypatch, make_event("猪", group_id=30000), runtime)
    other = await call_handler(plugin, monkeypatch, make_event("猪", group_id=30001), runtime)

    assert len(first.sent) == 1
    assert second.sent == []
    assert len(other.sent) == 1


async def test_handler_skips_when_service_missing(plugin, monkeypatch) -> None:
    runtime = SimpleNamespace(pig_meme=None)
    matcher = await call_handler(plugin, monkeypatch, make_event("猪"), runtime)
    assert matcher.sent == []


async def test_handler_skips_when_library_empty(plugin, monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    runtime = SimpleNamespace(pig_meme=PigMemeService(root))
    matcher = await call_handler(plugin, monkeypatch, make_event("猪"), runtime)
    assert matcher.sent == []


async def test_handler_swallows_send_failure(plugin, monkeypatch, tmp_path: Path) -> None:
    root = make_images(tmp_path, 1)
    runtime = SimpleNamespace(pig_meme=PigMemeService(root, max_bytes=1024))
    matcher = await call_handler(
        plugin, monkeypatch, make_event("猪"), runtime, fail_send=True
    )
    assert matcher.sent == []
