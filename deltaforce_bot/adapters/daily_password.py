"""免费每日密码数据源客户端。

优先使用 4399 游戏盒「三角洲行动 · 每日密码」工具页背后的公开 JSON：
字段最全（密码、地点说明、配图），且数据在北京时间 0 点更新。
失败时退回 eleven.icu 的公开接口（同样免费、无需 token）。

两个源都是第三方/非官方数据，随时可能变更或停服；本层把它们统一成
``DailyPasswordSnapshot``，换源只改这一层。
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from deltaforce_bot.config import Settings
from deltaforce_bot.models.daily_password import DailyPasswordEntry, DailyPasswordSnapshot

logger = logging.getLogger(__name__)

SOURCE_4399 = "4399"
SOURCE_ELEVEN = "eleven"
SOURCE_LABELS = {
    SOURCE_4399: "4399游戏盒",
    SOURCE_ELEVEN: "eleven.icu",
}

URL_4399 = "https://huodong2.4399.com/gameboxtools/sjzmrmm/api/password.php"
PAGE_4399 = "https://huodong2.4399.com/gameboxtools/sjzmrmm/"
ELEVEN_BASES = ("https://dfapi1.eleven.icu", "https://dfapi.eleven.icu")

# 展示顺序；未知地图排在最后
MAP_ORDER = ("零号大坝", "长弓溪谷", "巴克什", "航天基地", "潮汐监狱", "AZ3")
_ELEVEN_NAMES = {
    "db": "零号大坝",
    "cgxg": "长弓溪谷",
    "bks": "巴克什",
    "htjd": "航天基地",
    "cxjy": "潮汐监狱",
    "az3": "AZ3",
}

_CODE_PATTERN = re.compile(r"^\d{4}$")
_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DailyPasswordError(Exception):
    """所有数据源都不可用或返回内容不符合预期。"""


class DailyPasswordSourceError(DailyPasswordError):
    """单个数据源请求/解析失败。"""


class DailyPasswordClient:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=settings.daily_password_timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            },
        )
        self._tz = timezone(timedelta(hours=settings.daily_password_tz_offset))

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def fetch(self, day: str) -> DailyPasswordSnapshot:
        """按配置顺序尝试各数据源，返回 day（北京时间）当天的快照。"""
        sources = self._settings.daily_password_source_list
        if not sources:
            raise DailyPasswordError("未配置每日密码数据源")

        errors: list[str] = []
        for source in sources:
            try:
                snapshot = await self._fetch_source(source)
            except Exception as exc:  # noqa: BLE001  单个源失败不能影响换源
                logger.warning(
                    "每日密码源 %s 获取失败：%s: %s",
                    source,
                    exc.__class__.__name__,
                    exc,
                )
                errors.append(f"{source}: {exc}")
                continue

            if snapshot.day != day:
                logger.warning(
                    "每日密码源 %s 返回的日期是 %s，不是今天 %s，跳过",
                    source,
                    snapshot.day,
                    day,
                )
                errors.append(f"{source}: 数据日期 {snapshot.day} 不是 {day}")
                continue

            if not snapshot.entries:
                errors.append(f"{source}: 没有有效密码")
                continue

            return snapshot

        raise DailyPasswordError("所有每日密码数据源均不可用：" + "；".join(errors))

    async def _fetch_source(self, source: str) -> DailyPasswordSnapshot:
        if source == SOURCE_4399:
            return await self._fetch_4399()
        if source == SOURCE_ELEVEN:
            return await self._fetch_eleven()
        raise DailyPasswordSourceError(f"未知数据源 {source}")

    # ---------- 4399 游戏盒 ----------
    async def _fetch_4399(self) -> DailyPasswordSnapshot:
        try:
            response = await self._http.get(
                URL_4399,
                headers={"Referer": PAGE_4399},
            )
        except httpx.HTTPError as exc:
            raise DailyPasswordSourceError(f"网络异常：{exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            raise DailyPasswordSourceError(f"HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise DailyPasswordSourceError("返回内容不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise DailyPasswordSourceError("返回内容不是对象")

        code = body.get("code")
        if str(code) != "0":
            raise DailyPasswordSourceError(f"返回错误 code={code}")

        data = body.get("data")
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise DailyPasswordSourceError("缺少 data.rows")

        entries: list[DailyPasswordEntry] = []
        updated_values: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            password = str(row.get("pwd") or "").strip()
            if not name or not _CODE_PATTERN.fullmatch(password):
                continue
            entries.append(
                DailyPasswordEntry(
                    name=name,
                    code=password,
                    location=str(row.get("desc") or "").strip(),
                    image_url=_normalize_image_url(str(row.get("pic") or "")),
                )
            )
            if row.get("updated_at"):
                updated_values.append(str(row["updated_at"]))

        if not entries:
            raise DailyPasswordSourceError("没有有效密码")

        day = str(body.get("date") or "").strip()
        if not _DAY_PATTERN.fullmatch(day):
            day = _parse_utc_iso(updated_values[0], self._tz).date().isoformat() if updated_values else ""

        updated_at = _format_latest(updated_values, self._tz) or f"{day}T00:00:00"
        return DailyPasswordSnapshot(
            day=day,
            source=SOURCE_LABELS[SOURCE_4399],
            updated_at=updated_at,
            entries=_sort_entries(entries),
        )

    # ---------- eleven.icu ----------
    async def _fetch_eleven(self) -> DailyPasswordSnapshot:
        last_error: Exception | None = None
        for base in ELEVEN_BASES:
            try:
                return await self._fetch_eleven_base(base)
            except Exception as exc:  # noqa: BLE001  两个域名互为备份
                last_error = exc
                logger.debug("eleven.icu 域名 %s 失败：%s: %s", base, exc.__class__.__name__, exc)
        raise DailyPasswordSourceError(
            f"eleven.icu 请求失败：{last_error.__class__.__name__ if last_error else '未知错误'}"
        )

    async def _fetch_eleven_base(self, base: str) -> DailyPasswordSnapshot:
        try:
            response = await self._http.get(f"{base}/getOVData")
        except httpx.HTTPError as exc:
            raise DailyPasswordSourceError(f"网络异常：{exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            raise DailyPasswordSourceError(f"HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise DailyPasswordSourceError("返回内容不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise DailyPasswordSourceError("返回内容不是对象")

        data = body.get("data")
        bd_data = data.get("bdData") if isinstance(data, dict) else None
        if not isinstance(bd_data, dict):
            raise DailyPasswordSourceError("缺少 data.bdData")

        entries: list[DailyPasswordEntry] = []
        latest: datetime | None = None
        for key, info in bd_data.items():
            if not isinstance(info, dict):
                continue
            name = _ELEVEN_NAMES.get(str(key).lower())
            password = str(info.get("password") or "").strip()
            if not name or not _CODE_PATTERN.fullmatch(password):
                continue
            entries.append(DailyPasswordEntry(name=name, code=password))
            updated = _parse_compact_time(info.get("updated"), self._tz)
            if updated is not None and (latest is None or updated > latest):
                latest = updated

        if not entries:
            raise DailyPasswordSourceError("没有有效密码")
        if latest is None:
            raise DailyPasswordSourceError("缺少更新时间")

        return DailyPasswordSnapshot(
            day=latest.date().isoformat(),
            source=SOURCE_LABELS[SOURCE_ELEVEN],
            updated_at=latest.strftime("%Y-%m-%dT%H:%M:%S"),
            entries=_sort_entries(entries),
        )


def _sort_entries(entries: Iterable[DailyPasswordEntry]) -> tuple[DailyPasswordEntry, ...]:
    order = {name: index for index, name in enumerate(MAP_ORDER)}
    seen: set[str] = set()
    result: list[DailyPasswordEntry] = []
    for entry in sorted(
        entries,
        key=lambda item: (order.get(item.name, len(MAP_ORDER)), item.name),
    ):
        if entry.name in seen:
            continue
        seen.add(entry.name)
        result.append(entry)
    return tuple(result)


def _normalize_image_url(raw: str) -> str:
    url = html.unescape((raw or "").strip())
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://huodong2.4399.com" + url
    return url


def _parse_utc_iso(raw: str, tz: timezone) -> datetime:
    text = (raw or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyPasswordSourceError(f"无法解析更新时间 {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def _format_latest(values: Iterable[str], tz: timezone) -> str:
    latest: datetime | None = None
    for raw in values:
        try:
            parsed = _parse_utc_iso(raw, tz)
        except DailyPasswordSourceError:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest.strftime("%Y-%m-%dT%H:%M:%S") if latest else ""


def _parse_compact_time(raw: Any, tz: timezone) -> datetime | None:
    """解析 eleven.icu 的 20261023000001 形式（北京时间本地时间）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt, length in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        if len(text) >= length:
            try:
                return datetime.strptime(text[:length], fmt)
            except ValueError:
                continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(tz).replace(tzinfo=None)
    return parsed
