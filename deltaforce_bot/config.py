"""运行配置：全部通过环境变量 / .env 注入，敏感项不入库。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据源（三角洲数据帝开放平台）
    orzice_token: str = ""
    orzice_base_url: str = "https://orzice.com/workApi"
    orzice_timeout: float = 15.0
    orzice_daily_quota: int = 2000

    # 缓存时长（秒）：卡战备方案默认缓存 4 小时
    # 依据：平台按请求次数计费，而已确认战备价格在 4 小时内变化不大
    cache_ttl_tier: int = 14400
    # DIY 自定义凑战备（成本 10 次/请求）
    cache_ttl_diy: int = 3600

    # 功能开关
    custom_enabled: bool = False  # 凑战备（DIY）成本高，默认关闭

    # 限流（每分钟次数）
    rate_limit_user: int = 2
    rate_limit_group: int = 10
    debug_groups: str = ""

    # 掉线监控与告警（见 watchdog.py）
    alert_qq: str = ""  # 接收掉线/恢复通知的管理员 QQ，英文逗号分隔
    alert_webhook_url: str = ""  # 可选：外部告警地址（Server酱/飞书/钉钉/通用 JSON）
    alert_webhook_format: str = "json"  # json | serverchan | feishu | dingtalk
    alert_min_downtime: int = 60  # 离线不超过这个秒数不通知（避免网络抖动误报）

    # 展示
    alternatives_default: int = 3  # 除了最省方案，另外列出几条「配装思路」

    # 卡战备入口配置（地图 + 难度 → 接口档位）
    gates_config: Path = PROJECT_ROOT / "config" / "gates.json"

    @property
    def debug_group_set(self) -> set[int]:
        ids: set[int] = set()
        for part in self.debug_groups.replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
