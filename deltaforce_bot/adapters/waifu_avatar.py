"""QQ 头像下载：qlogo 公开接口，无需登录。"""

from __future__ import annotations

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
PRIMARY_URL = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
FALLBACK_URL = "https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")


class WaifuAvatarError(RuntimeError):
    """头像下载失败或返回的不是图片。"""


class WaifuAvatarClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        max_bytes: int = 5 * 1024 * 1024,
        user_agent: str = DEFAULT_USER_AGENT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_bytes = max(int(max_bytes), 1024)
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Referer": "https://q.qq.com/"},
        )

    async def fetch(self, user_id: int) -> bytes:
        last_error: Exception | None = None
        for template in (PRIMARY_URL, FALLBACK_URL):
            url = template.format(user_id=int(user_id))
            try:
                response = await self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            data = response.content
            if not data or len(data) > self.max_bytes:
                last_error = WaifuAvatarError(
                    f"头像大小异常：{len(data)} bytes（上限 {self.max_bytes}）"
                )
                continue
            if not data.startswith(_IMAGE_MAGIC):
                last_error = WaifuAvatarError("头像响应不是图片")
                continue
            return data

        raise WaifuAvatarError(f"下载 QQ {user_id} 头像失败：{last_error}")

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    def __repr__(self) -> str:  # 测试/日志友好，不暴露内部 client
        return f"WaifuAvatarClient(max_bytes={self.max_bytes})"
