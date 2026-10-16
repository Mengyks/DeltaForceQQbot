"""连接看护：记录与 QQ 的连线状态，掉线写日志、恢复后主动通知管理员。

为什么要这么做：QQ 掉线时，机器人账号本身已经失效，**没有任何办法通过 QQ 通知你**。
因此：

- 掉线瞬间：写日志（唯一信号），并在配置了 webhook 时推送外部告警（真正的"掉线即通知"）；
- 恢复瞬间：给管理员 QQ 发私聊，说明离线时长与原因（用现成的 QQ 通道，不需要任何外部服务）。

框架相关代码集中在 install_watchdog()，状态与文案是纯逻辑，可离线单测。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

# 注意：这两个必须放在模块顶层。启用 from __future__ import annotations 后注解是字符串，
# NoneBot 注册 handler 时会在**模块全局命名空间**里解析它；写成函数内导入会导致
# NameError: name 'NoticeEvent' is not defined（已在真实部署中踩到，见 tests/test_watchdog.py 的回归测试）。
from nonebot.adapters.onebot.v11 import Bot, NoticeEvent

from deltaforce_bot.config import Settings
from deltaforce_bot.services.text import humanize_duration

logger = logging.getLogger(__name__)


@dataclass
class ConnectionState:
    """连线状态与掉线信息。"""

    online: bool = False
    online_since: float = field(default_factory=time.time)
    offline_since: float | None = None
    offline_reason: str | None = None
    offline_count: int = 0  # 本进程启动以来累计掉线次数


class ConnectionWatchdog:
    """纯逻辑的连线看护：记录状态、算离线时长、生成通知文案。"""

    def __init__(self, time_fn: Callable[[], float] = time.time) -> None:
        self._time = time_fn
        self.state = ConnectionState(online_since=self._time())

    def mark_online(self) -> None:
        self.state.online = True
        self.state.online_since = self._time()

    def mark_offline(self) -> None:
        self.state.online = False
        self.state.offline_since = self._time()
        self.state.offline_count += 1

    def record_reason(self, reason: str) -> None:
        """记录 QQ 给出的下线原因（来自 bot_offline 通知）。"""
        self.state.offline_reason = reason

    def take_offline_summary(self) -> tuple[float, str | None]:
        """取出本次离线时长与原因，并把原因清空（避免下次重复使用旧原因）。"""
        offline_since = self.state.offline_since
        downtime = (self._time() - offline_since) if offline_since is not None else 0.0
        reason, self.state.offline_reason = self.state.offline_reason, None
        self.state.offline_since = None
        return max(downtime, 0.0), reason

    def online_duration(self) -> float:
        return max(self._time() - self.state.online_since, 0.0)


def format_recovery_message(*, downtime: float, reason: str | None, count: int) -> str:
    """恢复通知文案（发给管理员 QQ）。"""
    lines = [f"⚠️ 我刚刚离线了 {humanize_duration(downtime)}，现在已恢复。"]
    if reason:
        lines.append(f"原因（QQ 侧）：{reason}")
    if count > 1:
        lines.append(f"本次运行期间累计掉线 {count} 次。")
    lines.append("提示：如果原因是「登录已失效」，需要在 NapCat WebUI 重新扫码；"
                 "配置密码回退登录（ACCOUNT + NAPCAT_QUICK_PASSWORD）可自动恢复。")
    return "\n".join(lines)


def build_webhook_payload(fmt: str, title: str, text: str) -> dict[str, Any]:
    """把告警转成不同渠道需要的 body 结构。"""
    if fmt == "serverchan":
        return {"title": title, "desp": text}
    if fmt == "feishu":
        return {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    if fmt == "dingtalk":
        return {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    return {"title": title, "text": text}  # 通用 JSON


async def push_webhook(settings: Settings, *, title: str, text: str) -> None:
    """外部告警通道（可选）。失败只记日志，绝不影响主流程。"""
    url = (settings.alert_webhook_url or "").strip()
    if not url:
        return
    payload = build_webhook_payload(settings.alert_webhook_format, title, text)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
        logger.info("告警已推送（HTTP %s）", response.status_code)
    except Exception as exc:  # noqa: BLE001  告警通道不稳定不能影响机器人
        logger.warning("告警推送失败：%s: %s", exc.__class__.__name__, exc)


def parse_alert_users(text: str) -> list[int]:
    users: list[int] = []
    for part in (text or "").replace("，", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            users.append(int(part))
    return users


def install_watchdog(settings: Settings):
    """装配到 NoneBot：注册连线事件与 bot_offline 通知处理。

    只在 bot.py 里调用（需要 NoneBot 已初始化）。
    """
    from nonebot import get_driver, on_notice

    driver = get_driver()
    watchdog = ConnectionWatchdog()
    alert_users = parse_alert_users(settings.alert_qq)

    async def notify_recovered(bot: Bot) -> None:
        downtime, reason = watchdog.take_offline_summary()
        if downtime < settings.alert_min_downtime:
            return
        text = format_recovery_message(downtime=downtime, reason=reason, count=watchdog.state.offline_count)
        logger.warning("检测到离线 %.0f 秒后恢复（原因：%s）", downtime, reason or "未知")
        for user_id in alert_users:
            try:
                await bot.send_private_msg(user_id=user_id, message=text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("给 %s 发送恢复通知失败：%s", user_id, exc.__class__.__name__)
        await push_webhook(settings, title="三角洲机器人已恢复", text=text)

    @driver.on_bot_connect
    async def _on_connect(bot: Bot) -> None:
        logger.info("机器人 %s 已上线（离线状态由看护记录）", bot.self_id)
        await notify_recovered(bot)
        watchdog.mark_online()

    @driver.on_bot_disconnect
    async def _on_disconnect(bot: Bot) -> None:
        watchdog.mark_offline()
        logger.warning("机器人 %s 已离线，累计第 %s 次", bot.self_id, watchdog.state.offline_count)
        await push_webhook(
            settings,
            title="三角洲机器人已离线",
            text=f"机器人 {bot.self_id} 与 QQ 的连接已断开。若长时间不恢复，请检查 NapCat（journalctl -u napcat）。",
        )

    try:
        offline_notice = on_notice(priority=1, block=False)

        @offline_notice.handle()
        async def _handle_offline_notice(event: NoticeEvent) -> None:
            data: dict[str, Any] = {}
            try:
                data = event.model_dump() if hasattr(event, "model_dump") else event.dict()
            except Exception:  # noqa: BLE001
                data = {}
            if data.get("notice_type") == "bot_offline":
                reason = str(data.get("message") or data.get("tag") or "账号被下线")
                watchdog.record_reason(reason)
                logger.error("QQ 下线通知：%s", reason)
                await push_webhook(settings, title="三角洲机器人的 QQ 账号被下线", text=reason)
    except Exception as exc:  # noqa: BLE001
        # 看护是辅助功能：即使这部分注册失败，也不能影响机器人本体
        logger.error("bot_offline 通知处理器注册失败（不影响看护的其他部分）：%s: %s", exc.__class__.__name__, exc)

    logger.info("连接看护已启用（管理员告警 QQ：%s，webhook：%s）",
                alert_users or "未配置", "已配置" if settings.alert_webhook_url else "未配置")
    return watchdog
