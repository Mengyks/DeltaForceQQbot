"""ChatService 业务层测试：触发、窗口、上下文、图片、摘要/清理。"""

from __future__ import annotations

import time

import httpx
import pytest

from deltaforce_bot.adapters.chat_memory import ChatMemory
from deltaforce_bot.adapters.llm import LLMError, LLMResult
from deltaforce_bot.adapters.ratelimit import SlidingWindowLimiter
from deltaforce_bot.models.chat import ChatIncoming, IncomingImage, SummaryResult
from deltaforce_bot.runtime import Runtime
from deltaforce_bot.services.chat import (
    ChatBudgetExceeded,
    ChatEmptyContext,
    ChatRateLimited,
    ChatService,
    split_reply,
)
from tests.support import make_chat_settings


class FakeLLM:
    available = True

    def __init__(self, text: str = "我在呢", *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs) -> LLMResult:
        self.calls.append(messages)
        if self.error:
            raise self.error
        return LLMResult(text=self.text, prompt_tokens=10, completion_tokens=5, model="test-model")


def make_meta(
    *,
    group_id: int = 30000,
    user_id: int = 1,
    nickname: str = "甲",
    text: str = "你好",
    message_id: int = 1,
    addressed: bool = True,
    reply_to_bot: bool = False,
    images: tuple[IncomingImage, ...] = (),
    is_command: bool = False,
    is_bot: bool = False,
) -> ChatIncoming:
    return ChatIncoming(
        group_id=group_id,
        user_id=user_id,
        nickname=nickname,
        text=text,
        message_id=message_id,
        timestamp=1000.0,
        addressed=addressed,
        reply_to_bot=reply_to_bot,
        images=images,
        is_command=is_command,
        is_bot=is_bot,
    )


def make_service(tmp_path, settings=None, client=None, clock=None, image_http=None) -> tuple[ChatService, ChatMemory, FakeLLM]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = settings or make_chat_settings(tmp_path)
    (tmp_path / "persona.md").write_text("你是群里的测试机器人。", encoding="utf-8")
    memory = ChatMemory(settings.chat_db_path)
    fake = client or FakeLLM()
    service = ChatService(
        settings,
        fake,
        memory,
        SlidingWindowLimiter(settings.chat_rate_user),
        SlidingWindowLimiter(settings.chat_rate_group),
        clock=clock or (lambda: 1000.0),
        image_http=image_http,
    )
    return service, memory, fake


async def test_direct_message_generates_context_and_stores_usage(tmp_path) -> None:
    service, memory, fake = make_service(tmp_path)
    now = [1000.0]
    service._clock = lambda: now[0]  # type: ignore[attr-defined]
    try:
        await service.record_message(make_meta(text="今晚打什么图", message_id=1))
        reply = await service.handle(make_meta(text="你呢", message_id=2))
        assert reply.text == "我在呢"
        assert reply.prompt_tokens == 10
        assert reply.completion_tokens == 5

        messages = fake.calls[-1]
        assert messages[0]["role"] == "system"
        assert "你是群里的测试机器人" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "甲: 今晚打什么图"}
        assert messages[-1] == {"role": "user", "content": "甲: 你呢"}
        day = time.strftime("%Y-%m-%d", time.localtime(1000.0))
        assert memory.tokens_today(day) == 15
    finally:
        await service.aclose()
        memory.close()


async def test_followup_window_counts_and_resets(tmp_path) -> None:
    service, memory, _ = make_service(tmp_path, settings=make_chat_settings(tmp_path, chat_cooldown=0))
    now = [1000.0]
    service._clock = lambda: now[0]  # type: ignore[attr-defined]
    try:
        direct = make_meta(addressed=True, message_id=1)
        follow = make_meta(addressed=False, message_id=2, text="继续")
        assert service.should_respond(direct, now=now[0])
        await service.commit_reply(direct, "开窗", now=now[0])

        for _ in range(3):
            assert service.should_respond(follow, now=now[0] + 1)
            await service.commit_reply(follow, "跟进", now=now[0] + 1)
            now[0] += 1

        assert not service.should_respond(follow, now=now[0] + 1)
        assert service.should_respond(direct, now=now[0] + 1)  # @ 可以重新开窗
        await service.commit_reply(direct, "重新开窗", now=now[0] + 1)
        assert service.should_respond(follow, now=now[0] + 2)
    finally:
        await service.aclose()
        memory.close()


async def test_followup_window_expires_and_cooldown(tmp_path) -> None:
    settings = make_chat_settings(tmp_path, chat_cooldown=20, chat_followup_window=30)
    service, memory, _ = make_service(tmp_path, settings=settings)
    now = [1000.0]
    service._clock = lambda: now[0]  # type: ignore[attr-defined]
    try:
        direct = make_meta(addressed=True, message_id=1)
        follow = make_meta(addressed=False, message_id=2, text="继续")
        await service.commit_reply(direct, "开窗", now=now[0])
        assert not service.should_respond(follow, now=now[0] + 10)  # 冷却
        assert service.should_respond(follow, now=now[0] + 20)
        assert not service.should_respond(follow, now=now[0] + 31)  # 窗口过期
    finally:
        await service.aclose()
        memory.close()


async def test_group_allowlist_and_command_exclusion(tmp_path) -> None:
    service, memory, _ = make_service(tmp_path)
    try:
        assert service.is_chat_group(30000)
        assert not service.is_chat_group(40000)
        assert not service.should_respond(make_meta(group_id=40000))
        assert not service.should_respond(make_meta(is_command=True))
        assert not service.should_respond(make_meta(is_bot=True))
    finally:
        await service.aclose()
        memory.close()


async def test_rate_limit_and_budget(tmp_path) -> None:
    settings = make_chat_settings(tmp_path, chat_rate_user=1, chat_rate_group=100, chat_daily_token_budget=5)
    service, memory, _ = make_service(tmp_path, settings=settings)
    try:
        await service.handle(make_meta(message_id=1))
        with pytest.raises(ChatRateLimited):
            await service.handle(make_meta(message_id=2))

        # 新服务绕过限流器，但预算仍会拦截
        settings2 = make_chat_settings(
            tmp_path / "second",
            chat_rate_user=1,
            chat_rate_group=100,
            chat_daily_token_budget=5,
        )
        service2, memory2, _ = make_service(tmp_path / "second", settings=settings2)
        try:
            day = time.strftime("%Y-%m-%d", time.localtime(1000.0))
            memory2.add_usage(day, prompt_tokens=5, completion_tokens=0)
            with pytest.raises(ChatBudgetExceeded):
                await service2.handle(make_meta(message_id=3))
        finally:
            await service2.aclose()
            memory2.close()
    finally:
        await service.aclose()
        memory.close()


async def test_vision_payload_and_image_fallback(tmp_path) -> None:
    service, memory, fake = make_service(tmp_path)
    try:
        image = IncomingImage(data=b"\x89PNG\r\n\x1a\npayload", mime="image/png")
        await service.handle(make_meta(text="看这个", images=(image,), message_id=1))
        content = fake.calls[-1][-1]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "甲: 看这个"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

        # 下载失败时降级为文字占位，不抛异常
        async def fail(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        settings = make_chat_settings(tmp_path / "fallback")
        service2, memory2, fake2 = make_service(
            tmp_path / "fallback", settings=settings, image_http=http
        )
        try:
            await service2.handle(
                make_meta(
                    text="看这个",
                    images=(IncomingImage(url="https://img.test/a.png"),),
                    message_id=2,
                )
            )
            assert fake2.calls[-1][-1] == {"role": "user", "content": "甲: 看这个 [图片]"}
        finally:
            await service2.aclose()
            memory2.close()
            await http.aclose()
    finally:
        await service.aclose()
        memory.close()


async def test_summary_success_and_failure_keeps_raw_on_failure(tmp_path) -> None:
    settings = make_chat_settings(tmp_path)
    service, memory, fake = make_service(tmp_path, settings=settings)
    try:
        await service.record_message(make_meta(text="旧话题", message_id=1))
        await service.record_message(make_meta(text="新话题", message_id=2))
        result = await service.summarize(30000, now=1000.0)
        assert isinstance(result, SummaryResult)
        assert result.summary == "我在呢"
        assert result.deleted_messages == 2
        assert memory.message_count(30000) == 0
        assert memory.get_summary(30000) == "我在呢"

        # 失败时不删除已有记录
        await service.record_message(make_meta(text="不要丢", message_id=3))
        failing = FakeLLM(error=LLMError("boom"))
        settings2 = make_chat_settings(tmp_path / "fail")
        service2, memory2, _ = make_service(tmp_path / "fail", settings=settings2, client=failing)
        try:
            await service2.record_message(make_meta(text="不要丢", message_id=4))
            with pytest.raises(LLMError):
                await service2.summarize(30000)
            assert memory2.message_count(30000) == 1
        finally:
            await service2.aclose()
            memory2.close()
    finally:
        await service.aclose()
        memory.close()


async def test_clear_removes_context(tmp_path) -> None:
    service, memory, _ = make_service(tmp_path)
    try:
        await service.record_message(make_meta(text="一", message_id=1))
        memory.set_summary(30000, "旧摘要")
        assert await service.clear(30000) == 1
        assert memory.message_count(30000) == 0
        assert memory.get_summary(30000) == ""
    finally:
        await service.aclose()
        memory.close()


def test_split_reply_limits_chunks() -> None:
    text = "\n".join(f"第{index}句" + "啊" * 20 for index in range(20))
    chunks = split_reply(text, limit=100, max_chunks=3)
    assert len(chunks) == 3
    assert all(len(chunk) <= 101 for chunk in chunks)
    assert chunks[-1].endswith("…")
    assert split_reply("   ", limit=10) == []


async def test_runtime_wires_chat_when_enabled(tmp_path) -> None:
    settings = make_chat_settings(tmp_path, render_enabled=False)
    runtime = Runtime(settings)
    try:
        assert runtime.chat is not None
        assert runtime.chat_memory is not None
        assert runtime.chat_client is not None
        assert runtime.chat.is_chat_group(30000)
        assert not runtime.chat.is_chat_group(40000)
    finally:
        await runtime.aclose()


async def test_ambient_mode_joins_without_mention(tmp_path) -> None:
    settings = make_chat_settings(
        tmp_path,
        chat_ambient_enabled=True,
        chat_ambient_cooldown=30,
        chat_ambient_min_chars=2,
        chat_followup_window=0,
        chat_cooldown=0,
    )
    service, memory, _ = make_service(tmp_path, settings=settings)
    now = [1000.0]
    service._clock = lambda: now[0]  # type: ignore[attr-defined]
    try:
        meta = make_meta(addressed=False, text="今天好累", message_id=1)
        assert service.should_respond(meta, now=now[0])
        await service.commit_reply(meta, "歇会儿", now=now[0])

        # 没有窗口，但有主动参与冷却
        assert not service.should_respond(meta, now=now[0] + 10)
        assert service.should_respond(meta, now=now[0] + 31)

        # 太短的消息不主动插话；图片可以
        assert not service.should_respond(make_meta(addressed=False, text="6", message_id=2), now=now[0] + 31)
        assert service.should_respond(
            make_meta(
                addressed=False,
                text="",
                message_id=3,
                images=(IncomingImage(data=b"\x89PNG\r\n\x1a\nx", mime="image/png"),),
            ),
            now=now[0] + 31,
        )
    finally:
        await service.aclose()
        memory.close()
