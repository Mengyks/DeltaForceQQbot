"""聊天插件接入测试：规则、优先级、权限与记录器。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from tests.support import PROJECT_ROOT

PLUGIN_PATH = PROJECT_ROOT / "plugins" / "llm_chat.py"


def make_event(
    text: str,
    *,
    user_id: int = 20000,
    self_id: int = 10000,
    group_id: int = 30000,
    message_id: int = 1,
    with_at: bool = False,
) -> GroupMessageEvent:
    message = Message()
    if with_at:
        message.append(MessageSegment.at(self_id))
    message.append(MessageSegment.text(text))
    return GroupMessageEvent(
        time=1000,
        self_id=self_id,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=message_id,
        user_id=user_id,
        message=message,
        original_message=message,
        raw_message=text,
        font=0,
        sender={},
        group_id=group_id,
    )


@pytest.fixture(scope="module")
def plugin():
    import nonebot

    loaded = nonebot.get_plugin("llm_chat")
    if loaded is not None:
        return loaded
    loaded = nonebot.load_plugin(PLUGIN_PATH)
    assert loaded is not None, "聊天插件加载失败"
    return loaded


def stub_runtime(plugin, *, should: bool = True, group: bool = True, aliases=(), ignore=()):
    class StubChat:
        def is_chat_group(self, group_id: int) -> bool:
            return group

        def should_respond(self, meta) -> bool:
            return should

        async def record_message(self, meta) -> None:
            self.recorded = meta

    chat = StubChat()
    runtime = SimpleNamespace(
        chat=chat,
        settings=SimpleNamespace(
            chat_alias_list=list(aliases),
            chat_ignore_qq_set=set(ignore),
        ),
    )
    plugin.module.get_runtime = lambda: runtime
    return chat


def test_matcher_priorities(plugin) -> None:
    assert plugin.module.chat_matcher.priority == 50
    assert plugin.module.chat_matcher.block is True
    assert plugin.module.context_matcher.priority == 9
    assert plugin.module.context_matcher.block is True


def test_command_detection(plugin) -> None:
    assert plugin.module._is_command("/卡战备 巴克什 绝密") is True
    assert plugin.module._is_command("卡战备 巴克什 绝密") is False
    assert plugin.module._is_command("上下文清理 提取摘要") is True
    assert plugin.module._is_command("每日密码") is True
    assert plugin.module._is_command("/每日密码") is True
    assert plugin.module._is_command("今天天气真好") is False


def test_at_bot_anywhere_and_alias(plugin) -> None:
    event = make_event("你看看这个", with_at=True)
    assert plugin.module._at_bot(event) is True

    stub_runtime(plugin, aliases=["小洲"])
    assert plugin.module._is_addressed(make_event("小洲在吗"), "小洲在吗") is True
    assert plugin.module._is_addressed(make_event("在吗"), "在吗") is False


def test_should_chat_uses_service_and_excludes_commands(plugin) -> None:
    stub_runtime(plugin, should=True)
    assert plugin.module._should_chat(make_event("在吗")) is True
    assert plugin.module._should_chat(make_event("/卡战备 巴克什 绝密")) is False
    assert plugin.module._should_chat(make_event("每日密码")) is False

    stub_runtime(plugin, should=False)
    assert plugin.module._should_chat(make_event("在吗")) is False


def test_context_command_requires_addressed_and_chat_group(plugin) -> None:
    stub_runtime(plugin, group=True)
    assert plugin.module._is_context_command(make_event("上下文清理 提取摘要", with_at=True)) is True
    assert plugin.module._is_context_command(make_event("上下文清理 提取摘要")) is False

    stub_runtime(plugin, group=False)
    assert plugin.module._is_context_command(make_event("上下文清理 直接清理", with_at=True)) is False


def test_superuser_permission(plugin, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin.module.nonebot,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(superusers={"12345"})),
    )
    assert plugin.module._is_superuser(make_event("x", user_id=12345)) is True
    assert plugin.module._is_superuser(make_event("x", user_id=54321)) is False


async def test_preprocessor_records_human_messages(plugin) -> None:
    chat = stub_runtime(plugin, group=True)
    event = make_event("随便聊聊", message_id=9)
    await plugin.module.record_chat_message(event)

    recorded = chat.recorded
    assert recorded.group_id == 30000
    assert recorded.user_id == 20000
    assert recorded.text == "随便聊聊"
    assert recorded.message_id == 9


async def test_preprocessor_skips_commands(plugin) -> None:
    chat = stub_runtime(plugin, group=True)
    event = make_event("/卡战备 巴克什 绝密", message_id=10)
    await plugin.module.record_chat_message(event)
    assert not hasattr(chat, "recorded")
