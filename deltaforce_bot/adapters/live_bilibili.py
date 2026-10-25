"""B 站直播状态客户端（只读公开接口）。

关键事实（2026-10-25 实测，本地与线上服务器一致）：

- B 站 WAF 会拦截 ``python-httpx/...`` 默认 User-Agent，返回 HTTP 412；
  换成普通 Chrome UA + ``Referer: https://live.bilibili.com/`` 后正常返回 200。
- 批量接口的参数必须是数组形式 ``uids[]=UID1&uids[]=UID2``；
  写成 ``uids=1,2`` 会返回 ``code=1 invalid params``。
- 登录、Cookie、WBI 都不需要；直播列表/搜索类接口才需要，本项目不使用。

这些事实由 tests/test_live_client.py 锁定，换接口只改本文件。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from deltaforce_bot.config import Settings
from deltaforce_bot.models.live import LiveStatus

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
LIVE_REFERER = "https://live.bilibili.com/"
BATCH_URL = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
CHUNK_SIZE = 100
MAX_COVER_BYTES = 4 * 1024 * 1024


class LiveApiError(RuntimeError):
    """B 站接口不可用、返回格式异常或业务 code 非 0。"""


class LiveBlockedError(LiveApiError):
    """HTTP 412/403：通常是 UA/风控/机房 IP 被拦截。"""


class LiveRiskControlError(LiveApiError):
    """业务 code=-352：B 站风控，通常需要 WBI/Cookie 或降低频率。"""


class BilibiliLiveClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        retry_delay: float = 0.6,
    ) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(
                float(getattr(settings, "live_timeout", 15.0) or 15.0),
                connect=min(float(getattr(settings, "live_timeout", 15.0) or 15.0), 8.0),
            ),
            follow_redirects=True,
        )
        self._retry_delay = max(float(retry_delay), 0.0)
        self.requests_sent = 0

    @property
    def user_agent(self) -> str:
        configured = str(getattr(self._settings, "live_user_agent", "") or "").strip()
        return configured or DEFAULT_USER_AGENT

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": LIVE_REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
            self._owns_http = False

    async def fetch_status(self, uid: str) -> LiveStatus | None:
        """查询单个 UID；没有直播间/不存在时返回 None。"""
        statuses = await self.fetch_statuses([uid])
        return statuses.get(str(uid))

    async def fetch_statuses(self, uids: list[str] | tuple[str, ...]) -> dict[str, LiveStatus]:
        """批量查询；返回 UID→状态，缺失的 UID 不会出现在结果里。"""
        unique: list[str] = []
        seen: set[str] = set()
        for raw in uids:
            uid = str(raw).strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            unique.append(uid)
        if not unique:
            return {}

        result: dict[str, LiveStatus] = {}
        for start in range(0, len(unique), CHUNK_SIZE):
            chunk = unique[start : start + CHUNK_SIZE]
            result.update(await self._fetch_chunk(chunk))
        return result

    async def _fetch_chunk(self, uids: list[str]) -> dict[str, LiveStatus]:
        params: list[tuple[str, str]] = [("uids[]", uid) for uid in uids]
        body = await self._request(params)
        data = body.get("data")
        if data is None:
            return {}
        if isinstance(data, dict):
            pairs = [(str(uid), raw) for uid, raw in data.items()]
        elif isinstance(data, list):
            pairs = [
                (str(raw.get("uid") or ""), raw)
                for raw in data
                if isinstance(raw, dict)
            ]
        else:
            raise LiveApiError("B站直播状态返回格式无法识别")

        result: dict[str, LiveStatus] = {}
        for uid, raw in pairs:
            if not uid or not isinstance(raw, dict):
                continue
            try:
                status = LiveStatus.from_api(uid, raw)
            except (TypeError, ValueError) as exc:
                logger.warning("B站直播状态解析失败（UID %s）：%s", uid, exc)
                continue
            result[uid] = status
        logger.debug("B站直播状态查询：请求 %s 个 UID，返回 %s 个", len(uids), len(result))
        return result

    async def _request(self, params: list[tuple[str, str]]) -> dict[str, Any]:
        last_error: LiveApiError | None = None
        for attempt in (1, 2):
            try:
                response = await self._http.get(BATCH_URL, params=params, headers=self._headers)
            except httpx.TimeoutException as exc:
                last_error = LiveApiError("B站直播接口请求超时")
                logger.warning("B站直播接口第 %s 次超时", attempt)
                if attempt == 1:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = LiveApiError(f"B站直播接口网络异常：{exc.__class__.__name__}")
                logger.warning("B站直播接口第 %s 次网络异常：%s", attempt, exc.__class__.__name__)
                if attempt == 1:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error from exc

            self.requests_sent += 1
            status = response.status_code
            if status in (403, 412):
                raise LiveBlockedError(
                    f"B站直播接口拒绝了请求（HTTP {status}），通常是 User-Agent 或机房 IP 风控"
                )
            if status == 429 or status >= 500:
                last_error = LiveApiError(f"B站直播接口暂时不可用（HTTP {status}）")
                if attempt == 1:
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_error
            if status >= 400:
                raise LiveApiError(f"B站直播接口返回 HTTP {status}")

            try:
                body = response.json()
            except ValueError as exc:
                raise LiveApiError("B站直播接口返回内容不是合法 JSON") from exc
            if not isinstance(body, dict):
                raise LiveApiError("B站直播接口返回内容不是对象")

            code = body.get("code")
            if str(code) != "0":
                if str(code) == "-352":
                    raise LiveRiskControlError("B站直播接口触发风控（code=-352）")
                message = body.get("message") or body.get("msg") or ""
                raise LiveApiError(f"B站直播接口返回错误 code={code} {message}".strip())
            return body

        raise last_error or LiveApiError("B站直播接口请求失败")

    async def download_cover(
        self,
        url: str,
        dest: Path,
        *,
        max_bytes: int = MAX_COVER_BYTES,
    ) -> Path | None:
        """下载直播封面到本地临时文件；失败只返回 None，不影响文字兜底。"""
        target = (url or "").strip()
        if not target.lower().startswith(("http://", "https://")):
            return None
        try:
            response = await self._http.get(target, headers=self._headers)
        except httpx.HTTPError as exc:
            logger.debug("直播封面下载失败：%s: %s", exc.__class__.__name__, exc)
            return None
        if response.status_code != 200:
            logger.debug("直播封面下载失败：HTTP %s", response.status_code)
            return None
        content_type = (response.headers.get("content-type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            logger.debug("直播封面不是图片：%s", content_type)
            return None
        if not response.content or len(response.content) > max_bytes:
            logger.debug("直播封面为空或过大：%s bytes", len(response.content))
            return None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        try:
            await asyncio.to_thread(dest.write_bytes, response.content)
        except OSError as exc:
            logger.debug("直播封面写入失败：%s", exc)
            return None
        return dest


def parse_batch_fixture(body: Any) -> dict[str, LiveStatus]:
    """测试/录制辅助：把完整响应体解析成状态 map（不发起请求）。"""
    if not isinstance(body, dict):
        raise LiveApiError("响应不是对象")
    data = body.get("data")
    if isinstance(data, dict):
        pairs = list(data.items())
    elif isinstance(data, list):
        pairs = [(str(item.get("uid") or ""), item) for item in data if isinstance(item, dict)]
    else:
        return {}
    result: dict[str, LiveStatus] = {}
    for uid, raw in pairs:
        if isinstance(raw, dict):
            try:
                result[str(uid)] = LiveStatus.from_api(str(uid), raw)
            except (TypeError, ValueError):
                continue
    return result
