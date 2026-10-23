"""OpenAI 兼容的 LLM 客户端（DeepSeek V4.1 Flash 等）。

只做一件事：把 messages 发到 ``{base_url}/chat/completions`` 并解析文本/usage。
不依赖厂商 SDK；httpx 已在本项目依赖中。非流式，便于 QQ 一次性发送。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败（网络、协议或服务端错误）。"""


class LLMUnavailable(LLMError):
    """没有配置 API key/model，聊天功能不可用。"""


class LLMAuthError(LLMError):
    """401/403，通常表示 key 无效或没有模型权限。"""


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    tool_calls: tuple[dict, ...] = ()
    assistant_message: dict | None = None


class LLMClient:
    def __init__(
        self,
        settings,
        *,
        http: httpx.AsyncClient | None = None,
        retry_delay: float = 0.5,
    ) -> None:
        self._settings = settings
        self._base_url = (getattr(settings, "llm_base_url", "") or "").rstrip("/")
        self._api_key = getattr(settings, "llm_api_key", "") or ""
        self._model = getattr(settings, "llm_model", "") or ""
        self._timeout = float(getattr(settings, "llm_timeout", 60.0) or 60.0)
        self._max_tokens = int(getattr(settings, "llm_max_output_tokens", 800) or 800)
        self._temperature = float(getattr(settings, "llm_temperature", 0.8) or 0.8)
        self._retry_delay = max(retry_delay, 0.0)
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 10.0)),
            follow_redirects=True,
        )

    @property
    def available(self) -> bool:
        return bool(self._base_url and self._api_key and self._model)

    async def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResult:
        if not self.available:
            raise LLMUnavailable("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 未配置")

        url = f"{self._base_url}/chat/completions"
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": int(max_tokens or self._max_tokens),
            "temperature": float(self._temperature if temperature is None else temperature),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: LLMError | None = None
        for attempt in range(2):
            try:
                response = await self._http.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                last_error = LLMError(f"LLM 请求超时（>{self._timeout:.0f}s）")
                if attempt == 0:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = LLMError(f"LLM 网络错误：{exc.__class__.__name__}")
                if attempt == 0:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error from exc

            status = response.status_code
            if status in (401, 403):
                raise LLMAuthError(f"LLM 鉴权失败（HTTP {status}）")
            if status == 429 or status >= 500:
                last_error = LLMError(f"LLM 服务暂时不可用（HTTP {status}）")
                if attempt == 0:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error
            if status >= 400:
                detail = (response.text or "")[:300].replace("\n", " ")
                raise LLMError(f"LLM 请求失败（HTTP {status}）：{detail}")

            try:
                data = response.json()
            except ValueError as exc:
                raise LLMError("LLM 返回了非 JSON 内容") from exc

            choices = data.get("choices") or []
            if not choices:
                raise LLMError("LLM 返回空 choices")
            message = (choices[0] or {}).get("message") or {}
            tool_calls = tuple(
                call for call in (message.get("tool_calls") or []) if isinstance(call, dict)
            )
            text = _content_to_text(message.get("content"))
            if not text:
                # 少数兼容接口把正文放在 reasoning_content；仅在 content 为空时兜底
                text = _content_to_text(message.get("reasoning_content"))
            if not text and not tool_calls:
                raise LLMError("LLM 返回空文本")
            assistant_message = {
                "role": "assistant",
                "content": message.get("content"),
            }
            if tool_calls:
                assistant_message["tool_calls"] = list(tool_calls)

            usage = data.get("usage") or {}
            return LLMResult(
                text=text.strip(),
                prompt_tokens=_as_int(usage.get("prompt_tokens")),
                completion_tokens=_as_int(usage.get("completion_tokens")),
                model=str(data.get("model") or self._model),
                tool_calls=tool_calls,
                assistant_message=assistant_message,
            )

        raise last_error or LLMError("LLM 请求失败")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
            self._owns_http = False


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "".join(parts)
    return ""


def _as_int(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
