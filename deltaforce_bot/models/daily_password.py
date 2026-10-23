"""每日密码的数据模型（与框架、网络解耦）。

一次抓取得到一份快照：日期、来源、数据源更新时间、各地图密码。
快照会原样 JSON 化后写入独立 SQLite，重启后同一天继续复用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DailyPasswordEntry:
    """一张地图的当日密码。"""

    name: str
    code: str
    location: str = ""
    image_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "code": self.code,
            "location": self.location,
            "image_url": self.image_url,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DailyPasswordEntry":
        return cls(
            name=str(data.get("name") or "").strip(),
            code=str(data.get("code") or "").strip(),
            location=str(data.get("location") or "").strip(),
            image_url=str(data.get("image_url") or "").strip(),
        )


@dataclass(frozen=True)
class DailyPasswordSnapshot:
    """某一天的完整密码快照。"""

    day: str  # 北京时间 YYYY-MM-DD
    source: str  # 展示用来源名，如「4399游戏盒」
    updated_at: str  # 北京时间 ISO（YYYY-MM-DDTHH:MM:SS），用于展示更新时间
    entries: tuple[DailyPasswordEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "source": self.source,
            "updated_at": self.updated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DailyPasswordSnapshot":
        raw_entries = data.get("entries") or []
        entries = tuple(
            DailyPasswordEntry.from_dict(item)
            for item in raw_entries
            if isinstance(item, Mapping)
        )
        return cls(
            day=str(data.get("day") or "").strip(),
            source=str(data.get("source") or "").strip(),
            updated_at=str(data.get("updated_at") or "").strip(),
            entries=entries,
        )

    @classmethod
    def from_json(cls, raw: str) -> "DailyPasswordSnapshot":
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError("每日密码缓存内容不是合法 JSON") from exc
        if not isinstance(data, Mapping):
            raise ValueError("每日密码缓存内容不是对象")
        return cls.from_dict(data)
