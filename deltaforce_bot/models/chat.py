"""群聊机器人的纯数据模型（不依赖 NoneBot/httpx）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingImage:
    """一条待发送给模型的消息图片。

    ``data`` 由插件层从 ``bot.get_image`` 或下载结果填充；``url`` 是 OneBot
    收到的图片地址。两者都没有时只保留 ``summary``（如表情包文字）。
    """

    url: str = ""
    file: str = ""
    summary: str = ""
    data: bytes | None = None
    mime: str = ""


@dataclass(frozen=True)
class ChatIncoming:
    """一条群消息（插件层从 OneBot 事件提取）。"""

    group_id: int
    user_id: int
    nickname: str
    text: str
    message_id: int
    timestamp: float
    addressed: bool = False
    reply_to_bot: bool = False
    reply_text: str = ""
    images: tuple[IncomingImage, ...] = ()
    is_command: bool = False
    is_bot: bool = False


@dataclass(frozen=True)
class ChatReply:
    """一次模型调用的结果；是否发送、是否提交由插件层决定。"""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    trigger: str = ""


@dataclass(frozen=True)
class StoredMessage:
    """SQLite 中的一条上下文消息。"""

    id: int
    group_id: int
    user_id: int
    nickname: str
    role: str
    content: str
    message_id: int | None
    created_at: float


@dataclass(frozen=True)
class SummaryResult:
    summary: str
    deleted_messages: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
