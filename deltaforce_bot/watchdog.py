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
    """单个 bot 的连线状态与掉线信息。"""

    bot_id: str = ""
    online: bool = False
    online_since: float = field(default_factory=time.time)
    offline_since: float | None = None
    offline_reason: str | None = None
    offline_count: int = 0  # 本进程启动以来该 bot 累计掉线次数


class ConnectionWatchdog:
    """连线看护：按 bot 分别记录状态、算离线时长、生成通知文案。

    为什么按 bot 分别记录：同一进程里可能连上多个 OneBot 客户端
    （例如联调用的假 NapCat），共用一个全局状态会互相冲掉对方的在线/离线记录
    （实测踩到：假客户端连上来后把真实机器人的状态改成了"已上线"）。
    """

    def __init__(self, time_fn: Callable[[], float] = time.time) -> None:
        self._time = time_fn
        self._states: dict[str, ConnectionState] = {}

    def state_of(self, bot_id: str = "") -> ConnectionState:
        key = str(bot_id)
        if key not in self._states:
            self._states[key] = ConnectionState(bot_id=key, online_since=self._time())
        return self._states[key]

    @property
    def state(self) -> ConnectionState:
        """默认 bot（空 key）的状态，兼容旧调用与简单场景。"""
        return self.state_of("")

    def mark_online(self, bot_id: str = "") -> tuple[float, str | None] | None:
        """标记上线。返回（离线时长, 原因）；**此前没有离线记录时返回 None**。

        返回 None 是刻意设计：进程刚启动后的第一次连接并没有"离线过"，
        不应该通知（否则每次重启都会误报"离线 0 秒"）。
        """
        state = self.state_of(bot_id)
        result: tuple[float, str | None] | None = None
        if state.offline_since is not None:
            result = (max(self._time() - state.offline_since, 0.0), state.offline_reason)
            state.offline_since = None
            state.offline_reason = None
        state.online = True
        state.online_since = self._time()
        return result

    def mark_offline(self, bot_id: str = "") -> int:
        """标记离线，返回该 bot 累计掉线次数。"""
        state = self.state_of(bot_id)
        state.online = False
        state.offline_since = self._time()
        state.offline_count += 1
        return state.offline_count

    def record_reason(self, reason: str, bot_id: str = "") -> None:
        """记录 QQ 给出的下线原因（来自 bot_offline 通知）。"""
        self.state_of(bot_id).offline_reason = reason

    def online_duration(self, bot_id: str = "") -> float:
        return max(self._time() - self.state_of(bot_id).online_since, 0.0)

    # 兼容旧接口
    def take_offline_summary(self) -> tuple[float, str | None]:
        result = self.mark_online("")
        return result if result is not None else (0.0, None)


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

    @driver.on_bot_connect
    async def _on_connect(bot: Bot) -> None:
        bot_id = str(bot.self_id)
        summary = watchdog.mark_online(bot_id)
        logger.info("机器人 %s 已上线", bot_id)
        if summary is None:
            return  # 本进程内此前没有离线记录（例如刚重启），不通知
        downtime, reason = summary
        if downtime < settings.alert_min_downtime:
            logger.info("离线 %.0f 秒后恢复，未达告警阈值（%s 秒），不通知", downtime, settings.alert_min_downtime)
            return
        state = watchdog.state_of(bot_id)
        text = format_recovery_message(downtime=downtime, reason=reason, count=state.offline_count)
        logger.warning("检测到离线 %.0f 秒后恢复（原因：%s）", downtime, reason or "未知")
        for user_id in alert_users:
            try:
                await bot.send_private_msg(user_id=user_id, message=text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("给 %s 发送恢复通知失败：%s", user_id, exc.__class__.__name__)
        await push_webhook(settings, title="三角洲机器人已恢复", text=text)

    @driver.on_bot_disconnect
    async def _on_disconnect(bot: Bot) -> None:
        bot_id = str(bot.self_id)
        count = watchdog.mark_offline(bot_id)
        logger.warning("机器人 %s 已离线，累计第 %s 次", bot_id, count)
        await push_webhook(
            settings,
            title="三角洲机器人已离线",
            text=f"机器人 {bot_id} 与 QQ 的连接已断开。若长时间不恢复，请检查 NapCat（journalctl -u napcat）。",
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
                watchdog.record_reason(reason, str(data.get("self_id") or ""))
                logger.error("QQ 下线通知：%s", reason)
                await push_webhook(settings, title="三角洲机器人的 QQ 账号被下线", text=reason)
    except Exception as exc:  # noqa: BLE001
        # 看护是辅助功能：即使这部分注册失败，也不能影响机器人本体
        logger.error("bot_offline 通知处理器注册失败（不影响看护的其他部分）：%s: %s", exc.__class__.__name__, exc)

    logger.info("连接看护已启用（管理员告警 QQ：%s，webhook：%s）",
                alert_users or "未配置", "已配置" if settings.alert_webhook_url else "未配置")
    return watchdog
