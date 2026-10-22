"""SQLite 群聊上下文测试（不联网、不启 NoneBot）。"""

from __future__ import annotations

import time

from deltaforce_bot.adapters.chat_memory import ChatMemory


def test_add_and_recent_messages_are_group_scoped(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        first = memory.add_message(30000, 1, "甲", "user", "第一句", message_id=101)
        second = memory.add_message(30000, 2, "乙", "user", "第二句", message_id=102)
        other = memory.add_message(40000, 3, "丙", "user", "别的群", message_id=103)

        assert first and second and other
        recent = memory.recent_messages(30000, limit=10, max_chars=1000)
        assert [item.content for item in recent] == ["第一句", "第二句"]
        assert recent[0].nickname == "甲"
        assert recent[1].message_id == 102

        filtered = memory.recent_messages(30000, limit=10, exclude_message_id=102)
        assert [item.content for item in filtered] == ["第一句"]
    finally:
        memory.close()


def test_recent_messages_char_budget_keeps_newest(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        for index in range(5):
            memory.add_message(30000, 1, "甲", "user", "x" * 10 + str(index), message_id=100 + index)

        recent = memory.recent_messages(30000, limit=10, max_chars=25)
        # 至少保留最新一条；预算只用于截断更老的消息
        assert len(recent) >= 1
        assert recent[-1].content.endswith("4")
    finally:
        memory.close()


def test_summary_and_delete_up_to_preserve_new_messages(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        memory.add_message(30000, 1, "甲", "user", "旧一", message_id=101)
        memory.add_message(30000, 1, "甲", "user", "旧二", message_id=102)
        max_id = memory.max_message_id(30000)
        memory.set_summary(30000, "这是摘要")
        memory.add_message(30000, 2, "乙", "user", "新消息", message_id=103)

        assert memory.get_summary(30000) == "这是摘要"
        deleted = memory.delete_up_to(30000, max_id)
        assert deleted == 2

        remaining = memory.recent_messages(30000, limit=10)
        assert [item.content for item in remaining] == ["新消息"]
        assert memory.get_summary(30000) == "这是摘要"
    finally:
        memory.close()


def test_clear_removes_messages_and_summary(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        memory.add_message(30000, 1, "甲", "user", "一", message_id=101)
        memory.add_message(30000, 1, "甲", "user", "二", message_id=102)
        memory.set_summary(30000, "摘要")

        assert memory.clear(30000) == 2
        assert memory.message_count(30000) == 0
        assert memory.get_summary(30000) == ""
    finally:
        memory.close()


def test_usage_tokens_and_prune(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        old = time.time() - 10 * 86400
        memory.add_message(30000, 1, "甲", "user", "旧消息", message_id=101, created_at=old)
        memory.add_message(30000, 1, "甲", "user", "新消息", message_id=102)
        memory.add_usage("2026-10-22", group_id=30000, prompt_tokens=10, completion_tokens=5)
        memory.add_usage("2026-10-22", group_id=30000, prompt_tokens=2, completion_tokens=3)

        assert memory.tokens_today("2026-10-22") == 20
        assert memory.prune_before(time.time() - 86400) == 1
        assert [item.content for item in memory.recent_messages(30000, limit=10)] == ["新消息"]
    finally:
        memory.close()


def test_duplicate_message_id_is_ignored(tmp_path) -> None:
    memory = ChatMemory(tmp_path / "chat.sqlite3")
    try:
        memory.add_message(30000, 1, "甲", "user", "一", message_id=101)
        memory.add_message(30000, 1, "甲", "user", "重复投递", message_id=101)
        assert memory.message_count(30000) == 1
    finally:
        memory.close()
