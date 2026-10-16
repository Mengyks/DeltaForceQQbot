"""图片卡片渲染：Jinja2 模板 → HTML → Playwright 无头浏览器 → PNG。

实测经验（务必保持）：
- 必须把 HTML 写成文件并用 ``page.goto(file://…)`` 打开；用 set_content 时页面没有 file:// 源，
  Chromium 会拦截所有本地图片（表现为裂图）。
- 图片路径要 URL 编码（中文/空格文件名）。
- 浏览器实例要复用：冷启动约 0.6 秒，之后每次渲染 0.4~0.6 秒。
- 渲染失败一律抛 RenderError，由上层降级回文字版，绝不能让图片问题导致命令无响应。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from deltaforce_bot.render.card import CardContext

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "card.html.j2"


class RenderError(RuntimeError):
    """渲染失败（模板、浏览器或超时）。"""


class CardRenderer:
    """无头浏览器渲染器：全局复用一个浏览器实例与页面。"""

    def __init__(
        self,
        *,
        timeout: float = 6.0,
        viewport_width: int = 1040,
        scale: int = 1,
        chromium_path: str = "",
    ) -> None:
        self._timeout = timeout
        self._chromium_path = (chromium_path or "").strip()
        # 视口高度给小值：full_page 截图会自动撑到内容高度，否则下方会留出大片空白
        self._viewport = {"width": viewport_width, "height": 200}
        self._scale = scale
        self._playwright = None
        self._browser = None
        self._page = None
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
        )
        self._lock = asyncio.Lock()

    # ---- 生命周期 ----
    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise RenderError("未安装 playwright（pip install playwright 并安装浏览器）") from exc

        self._playwright = await async_playwright().start()
        # 容器/服务器：显式指定系统 Chromium（apt 安装）；本地 Windows：退回到 Edge/Chrome
        # --no-sandbox 与 --disable-dev-shm-usage 是容器内跑 Chromium 的常规要求
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

        attempts: list[dict] = []
        if self._chromium_path:
            attempts.append({"executable_path": self._chromium_path, "args": launch_args})
        attempts += [
            {"channel": "msedge", "args": launch_args},
            {"channel": "chrome", "args": launch_args},
            {"args": launch_args},  # Playwright 自带 chromium（需 playwright install chromium）
        ]

        for options in attempts:
            try:
                self._browser = await self._playwright.chromium.launch(**options)
                logger.info("卡片渲染浏览器已启动：%s", options.get("executable_path") or options.get("channel") or "playwright-chromium")
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("浏览器启动失败 %s：%s", options, exc)
        if self._browser is None:
            raise RenderError("启动浏览器失败：请确认已安装 chromium（容器内 apt install chromium，并设置 RENDER_CHROMIUM_PATH）")

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

    # ---- 渲染 ----
    async def render(self, context: CardContext) -> bytes:
        if self._browser is None:
            await self.start()
        try:
            return await asyncio.wait_for(self._render_locked(context), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            await self._reset_page()
            raise RenderError(f"渲染超时（>{self._timeout}s）") from exc
        except RenderError:
            raise
        except Exception as exc:  # noqa: BLE001  统一转成 RenderError，让上层降级
            await self._reset_page()
            raise RenderError(f"渲染失败：{exc.__class__.__name__}: {exc}") from exc

    async def _render_locked(self, context: CardContext) -> bytes:
        async with self._lock:
            html = self._env.get_template(TEMPLATE_NAME).render(
                **{
                    **context.__dict__,
                    "slots_padded": context.slots_padded,
                }
            )
            html_path = Path(tempfile.gettempdir()) / "deltaforce_card.html"
            html_path.write_text(html, encoding="utf-8")

            page = await self._ensure_page()
            await page.goto(html_path.as_uri(), wait_until="load")
            await page.wait_for_timeout(120)  # 等字体与图片稳定
            return await page.screenshot(full_page=True)

    async def _ensure_page(self):
        if self._page is None:
            self._page = await self._browser.new_page(
                viewport=self._viewport, device_scale_factor=self._scale
            )
        return self._page

    async def _reset_page(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:  # noqa: BLE001
                pass
            self._page = None
