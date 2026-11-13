"""直播订阅的数据模型（与框架、网络解耦）。

只做 B 站：一个订阅就是一个 UID；`live_time` 是 B 站给出的本场开播时间，
用它作为“场次 ID”做去重，避免同一场直播重复推送。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Mapping

LIVE_STATUS_OFF = 0
LIVE_STATUS_LIVE = 1
LIVE_STATUS_ROUND = 2


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


@dataclass(frozen=True)
class LiveStatus:
    """一次 B 站直播状态查询的结果。"""

    uid: str
    room_id: str = ""
    uname: str = ""
    live_status: int = LIVE_STATUS_OFF
    live_time: int = 0
    title: str = ""
    cover: str = ""
    online: int = 0

    @property
    def is_live(self) -> bool:
        return self.live_status == LIVE_STATUS_LIVE

    @property
    def session_key(self) -> str:
        """同一场直播的稳定标识；没有 live_time 时退回固定值。"""
        return str(self.live_time) if self.live_time > 0 else "live"

    @property
    def room_url(self) -> str:
        if not self.room_id:
            return ""
        return f"https://live.bilibili.com/{self.room_id}"

    @property
    def status_text(self) -> str:
        if self.live_status == LIVE_STATUS_LIVE:
            return "直播中"
        if self.live_status == LIVE_STATUS_ROUND:
            return "轮播"
        return "未开播"

    @classmethod
    def from_api(cls, uid: str, raw: Mapping[str, Any]) -> "LiveStatus":
        if not isinstance(raw, Mapping):
            raise ValueError("B站直播状态格式异常")
        room_id = _to_text(raw.get("room_id"))
        if room_id == "0":
            room_id = ""
        return cls(
            uid=_to_text(raw.get("uid")) or _to_text(uid),
            room_id=room_id,
            uname=_to_text(raw.get("uname")),
            live_status=_to_int(raw.get("live_status")),
            live_time=_to_int(raw.get("live_time")),
            title=_to_text(raw.get("title")),
            cover=_to_text(raw.get("cover_from_user") or raw.get("cover")),
            online=_to_int(raw.get("online")),
        )


@dataclass(frozen=True)
class LiveState:
    """本地保存的最新状态；missing_count 用于避免接口临时缺数据就误判下播。"""

    status: LiveStatus
    checked_at: float
    missing_count: int = 0

    @property
    def uid(self) -> str:
        return self.status.uid

    @property
    def is_live(self) -> bool:
        return self.status.is_live


@dataclass(frozen=True)
class LiveSubscription:
    """某个群的一条直播订阅；同一个 UID 可以在多个群分别订阅。"""

    uid: str
    group_id: int = 0
    room_id: str = ""
    uname: str = ""
    added_by: int = 0
    added_at: float = 0.0
    enabled: bool = True

    @property
    def display_name(self) -> str:
        return self.uname or self.uid


@dataclass(frozen=True)
class LiveNotification:
    """一条待发送的开播通知（按群生成）。"""

    uid: str
    group_id: int
    session_key: str
    uname: str
    room_id: str
    title: str
    cover: str
    live_time: int
    live_status: int = LIVE_STATUS_LIVE

    @property
    def room_url(self) -> str:
        if not self.room_id:
            return ""
        return f"https://live.bilibili.com/{self.room_id}"

    @property
    def display_name(self) -> str:
        return self.uname or self.uid


@dataclass
class LivePollResult:
    checked: int = 0
    live: int = 0
    notifications: list[LiveNotification] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
