"""每日密码卡片数据装配：把当天快照整理成模板需要的地图分区。

原型要求（assets/items/html_prototype/每日密码原型.png）：
- 六张地图各占一块，使用对应地图背景；
- 展示密码的区域要压暗、与背景区分，密码字号大；
- 比例可按实际效果调整。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from deltaforce_bot.models.daily_password import DailyPasswordSnapshot
from deltaforce_bot.render.assets import AssetLibrary, file_url


@dataclass
class DailyPasswordCardEntry:
    index: str  # 01 ~ 06
    name: str
    code: str
    location: str
    background_url: str | None


@dataclass
class DailyPasswordCardContext:
    day: str
    source: str
    updated_at: str  # 展示用，如「00:00」
    push_time: str  # 展示用，如「11:00」
    entries: list[DailyPasswordCardEntry]


def build_daily_password_context(
    snapshot: DailyPasswordSnapshot,
    assets: AssetLibrary,
    *,
    push_time: str = "11:00",
) -> DailyPasswordCardContext:
    entries = [
        DailyPasswordCardEntry(
            index=f"{index:02d}",
            name=entry.name,
            code=entry.code,
            location=entry.location,
            background_url=file_url(assets.background(entry.name)),
        )
        for index, entry in enumerate(snapshot.entries, start=1)
    ]
    return DailyPasswordCardContext(
        day=snapshot.day,
        source=snapshot.source,
        updated_at=_display_update_time(snapshot.updated_at, snapshot.day),
        push_time=push_time,
        entries=entries,
    )


def _display_update_time(updated_at: str, day: str) -> str:
    text = (updated_at or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return "—"
    if parsed.date().isoformat() == day:
        return parsed.strftime("%H:%M")
    return parsed.strftime("%m-%d %H:%M")
