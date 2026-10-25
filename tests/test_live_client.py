"""B站直播客户端契约测试：UA、参数格式、解析、错误映射、重试。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from deltaforce_bot.adapters.live_bilibili import (
    BilibiliLiveClient,
    LiveApiError,
    LiveBlockedError,
    LiveRiskControlError,
)
from tests.support import load_body, make_live_settings


def make_client(tmp_path: Path, handler, **overrides):
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    settings = make_live_settings(tmp_path, **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return BilibiliLiveClient(settings, http=http, retry_delay=0), calls


async def test_batch_query_uses_browser_ua_and_array_params(tmp_path: Path) -> None:
    body = load_body("live_bilibili_batch_live.json", real=True)
    client, calls = make_client(tmp_path, lambda request, n: httpx.Response(200, json=body))

    result = await client.fetch_statuses(["3493280926927123", "672328094"])

    assert set(result) == {"3493280926927123", "672328094"}
    live = result["3493280926927123"]
    assert live.is_live is True
    assert live.live_time == 1792912598
    assert live.room_id == "1883376823"
    assert live.uname == "seven栗子"
    assert live.cover.endswith(".jpg")
    assert result["672328094"].live_status == 2
    assert result["672328094"].is_live is False

    request = calls[0]
    assert request.headers["user-agent"].startswith("Mozilla/5.0")
    assert request.headers["referer"] == "https://live.bilibili.com/"
    assert request.url.params.get_list("uids[]") == ["3493280926927123", "672328094"]


async def test_empty_batch_data_is_empty_result(tmp_path: Path) -> None:
    body = load_body("live_bilibili_batch_empty.json", real=True)
    client, _ = make_client(tmp_path, lambda request, n: httpx.Response(200, json=body))

    assert await client.fetch_statuses(["999999999999"]) == {}


async def test_missing_uid_returns_none(tmp_path: Path) -> None:
    body = load_body("live_bilibili_batch_empty.json", real=True)
    client, _ = make_client(tmp_path, lambda request, n: httpx.Response(200, json=body))

    assert await client.fetch_status("999999999999") is None


async def test_http_412_is_blocked_without_retry(tmp_path: Path) -> None:
    client, calls = make_client(
        tmp_path,
        lambda request, n: httpx.Response(412, text="<html>blocked</html>"),
    )

    with pytest.raises(LiveBlockedError):
        await client.fetch_statuses(["1"])

    assert len(calls) == 1


async def test_code_352_is_risk_control(tmp_path: Path) -> None:
    body = load_body("live_bilibili_error_352.json", real=True)
    client, calls = make_client(tmp_path, lambda request, n: httpx.Response(200, json=body))

    with pytest.raises(LiveRiskControlError):
        await client.fetch_statuses(["1"])

    assert len(calls) == 1


async def test_500_retries_once_then_succeeds(tmp_path: Path) -> None:
    body = load_body("live_bilibili_batch_live.json", real=True)
    client, calls = make_client(
        tmp_path,
        lambda request, n: httpx.Response(500) if n == 1 else httpx.Response(200, json=body),
    )

    result = await client.fetch_statuses(["3493280926927123"])

    assert result["3493280926927123"].is_live is True
    assert len(calls) == 2


async def test_network_error_retries_once_then_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client, calls = make_client(tmp_path, handler)

    with pytest.raises(LiveApiError):
        await client.fetch_statuses(["1"])

    assert len(calls) == 2


async def test_non_json_and_business_error(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request, n: httpx.Response(200, text="not json"))
    with pytest.raises(LiveApiError):
        await client.fetch_statuses(["1"])

    client, _ = make_client(
        tmp_path,
        lambda request, n: httpx.Response(200, json={"code": 1, "msg": "invalid params"}),
    )
    with pytest.raises(LiveApiError):
        await client.fetch_statuses(["1"])


async def test_large_uid_list_is_chunked(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        first = request.url.params.get_list("uids[]")[0]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    first: {
                        "uid": int(first),
                        "room_id": 1,
                        "uname": "u",
                        "live_status": 0,
                        "live_time": 0,
                    }
                },
            },
        )

    settings = make_live_settings(tmp_path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BilibiliLiveClient(settings, http=http, retry_delay=0)

    result = await client.fetch_statuses([str(i) for i in range(1, 151)])

    assert len(calls) == 2
    assert len(calls[0].url.params.get_list("uids[]")) == 100
    assert len(calls[1].url.params.get_list("uids[]")) == 50
    assert len(result) == 2


async def test_download_cover_rejects_non_image_and_writes_file(tmp_path: Path) -> None:
    client, _ = make_client(
        tmp_path,
        lambda request, n: httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nfake",
            headers={"content-type": "image/png"},
        ),
    )
    dest = tmp_path / "cover.png"
    assert await client.download_cover("https://i0.hdslb.com/cover.png", dest) == dest
    assert dest.read_bytes().startswith(b"\x89PNG")

    client, _ = make_client(
        tmp_path,
        lambda request, n: httpx.Response(
            200,
            content=b"<html>",
            headers={"content-type": "text/html"},
        ),
    )
    assert await client.download_cover("https://i0.hdslb.com/x", tmp_path / "x.jpg") is None
