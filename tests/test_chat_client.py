"""OpenAI 兼容客户端契约测试（MockTransport，不花真实 token）。"""

from __future__ import annotations

import json

import httpx
import pytest

from deltaforce_bot.adapters.llm import LLMAuthError, LLMClient, LLMError, LLMUnavailable
from tests.support import make_chat_settings


def _response(text: str = "你好") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
    )


async def test_success_sends_expected_request_and_parses_usage(tmp_path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response("收到")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = LLMClient(make_chat_settings(tmp_path), http=http, retry_delay=0)
        result = await client.chat([{"role": "user", "content": "在吗"}])

        assert result.text == "收到"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 7
        assert result.model == "test-model"

        assert len(requests) == 1
        request = requests[0]
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-llm-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["stream"] is False
        assert body["max_tokens"] == 100
        assert body["messages"] == [{"role": "user", "content": "在吗"}]
    finally:
        await http.aclose()


async def test_vision_content_list_is_passed_through(tmp_path) -> None:
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _response("看到了")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = LLMClient(make_chat_settings(tmp_path), http=http, retry_delay=0)
        content = [
            {"type": "text", "text": "这是什么"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
        await client.chat([{"role": "user", "content": content}])
        assert captured[0]["messages"][0]["content"] == content
    finally:
        await http.aclose()


async def test_retries_once_on_500(tmp_path) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, text="temporary")
        return _response("第二次成功")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = LLMClient(make_chat_settings(tmp_path), http=http, retry_delay=0)
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result.text == "第二次成功"
        assert attempts == 2
    finally:
        await http.aclose()


async def test_auth_error_is_not_retried(tmp_path) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": "bad key"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = LLMClient(make_chat_settings(tmp_path), http=http, retry_delay=0)
        with pytest.raises(LLMAuthError):
            await client.chat([{"role": "user", "content": "hi"}])
        assert attempts == 1
    finally:
        await http.aclose()


async def test_empty_choices_raise_llm_error(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = LLMClient(make_chat_settings(tmp_path), http=http, retry_delay=0)
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "hi"}])
    finally:
        await http.aclose()


async def test_missing_key_is_unavailable(tmp_path) -> None:
    client = LLMClient(make_chat_settings(tmp_path, llm_api_key=""), retry_delay=0)
    assert client.available is False
    with pytest.raises(LLMUnavailable):
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
