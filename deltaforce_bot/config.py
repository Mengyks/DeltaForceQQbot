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

    # 群聊机器人（v2）
    chat_enabled: bool = False
    chat_groups: str = ""  # 允许聊天的群，英文/中文逗号分隔；为空=关闭
    chat_aliases: str = ""  # 群里叫它的名字，逗号分隔
    chat_ignore_qq: str = ""  # 需要忽略的其他机器人/账号 QQ
    chat_db_path: Path = PROJECT_ROOT / "data" / "chat.sqlite3"
    chat_persona_file: Path = PROJECT_ROOT / "config" / "persona.md"
    chat_max_context_chars: int = 6000
    chat_max_input_chars: int = 2000
    chat_history_ttl_days: int = 7
    chat_followup_window: int = 180  # 秒；机器人回复后开启的跟进窗口
    chat_followup_max_replies: int = 3  # 窗口内最多再回几次（不含开窗那次）
    chat_cooldown: int = 20  # 秒；窗口内两次回复的最小间隔
    chat_ambient_enabled: bool = False  # 没有 @/窗口时，是否也主动参与群聊
    chat_ambient_cooldown: int = 30  # 秒；两次主动参与之间的最小间隔
    chat_ambient_min_chars: int = 2  # 主动参与时忽略更短的消息（"6"/"嗯"）
    chat_rate_user: int = 3  # 次/分钟
    chat_rate_group: int = 20  # 次/分钟
    chat_daily_token_budget: int = 200_000
    chat_vision_enabled: bool = True
    chat_image_max_bytes: int = 8 * 1024 * 1024
    chat_max_images_per_message: int = 4
    chat_reply_chunk_chars: int = 1500
    chat_max_reply_chunks: int = 3
    chat_concurrency: int = 3

    # 本地表情包（LLM 通过工具调用选择分类，服务随机挑一张）
    chat_meme_enabled: bool = True
    chat_meme_dir: Path = PROJECT_ROOT / "assets" / "memes"
    chat_meme_cooldown: int = 30  # 秒；同一群两次发图的最小间隔
    chat_meme_max_per_10min: int = 5
    chat_meme_max_bytes: int = 8 * 1024 * 1024
    chat_meme_recent_exclude: int = 8  # 避开最近发过的 N 张

    # 每日密码（v3）：免费数据源，同一天只取一次，独立 SQLite 缓存
    daily_password_enabled: bool = False
    daily_password_db_path: Path = PROJECT_ROOT / "data" / "daily_password.sqlite3"
    daily_password_groups: str = ""  # 定时推送群；为空时沿用 CHAT_GROUPS
    daily_password_push_enabled: bool = True
    daily_password_refresh_time: str = "00:05"  # 北京时间；预热当天缓存，供手动查询/11 点推送
    daily_password_push_time: str = "11:00"  # 北京时间；固定推送时间
    daily_password_push_window_minutes: int = 60  # 错过推送时间后的补推窗口
    daily_password_query_cooldown: int = 300  # 秒；同一群两次手动查询的最小间隔
    daily_password_sources: str = "4399,eleven"  # 按顺序尝试：4399 → eleven
    daily_password_timeout: float = 15.0
    daily_password_tz_offset: int = 8  # 服务器通常是 UTC，按 UTC+8 计算“今天”、0:05 和 11:00
    daily_password_history_days: int = 7  # 本地快照/推送记录保留天数

    # LLM（OpenAI 兼容接口）
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-flash"
    llm_timeout: float = 60.0
    llm_max_output_tokens: int = 800
    llm_temperature: float = 0.8
    llm_summary_max_tokens: int = 500

    # 掉线监控与告警（见 watchdog.py）
    alert_qq: str = ""  # 接收掉线/恢复通知的管理员 QQ，英文逗号分隔
    alert_webhook_url: str = ""  # 可选：外部告警地址（Server酱/飞书/钉钉/通用 JSON）
    alert_webhook_format: str = "json"  # json | serverchan | feishu | dingtalk
    alert_min_downtime: int = 60  # 离线不超过这个秒数不通知（避免网络抖动误报）

    # 展示
    alternatives_default: int = 3  # 除了最省方案，另外列出几条「配装思路」

    # 卡战备入口配置（地图 + 难度 → 接口档位）
    gates_config: Path = PROJECT_ROOT / "config" / "gates.json"

    # 图片卡片渲染
    render_enabled: bool = True  # 渲染失败会自动降级回文字版
    assets_dir: Path = PROJECT_ROOT / "assets" / "items"
    render_timeout: float = 6.0
    # 容器内用 apt 安装的系统 Chromium；本地 Windows 留空会自动尝试 Edge/Chrome
    render_chromium_path: str = ""
    image_cache_ttl: int = 14400  # 图片缓存 4 小时（发送成功后才写入）

    @property
    def debug_group_set(self) -> set[int]:
        ids: set[int] = set()
        for part in self.debug_groups.replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @staticmethod
    def _parse_ints(value: str) -> set[int]:
        ids: set[int] = set()
        for part in (value or "").replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @property
    def chat_group_set(self) -> set[int]:
        return self._parse_ints(self.chat_groups)

    @property
    def chat_alias_list(self) -> list[str]:
        aliases: list[str] = []
        for part in (self.chat_aliases or "").replace("，", ",").split(","):
            alias = part.strip()
            if alias and alias not in aliases:
                aliases.append(alias)
        return aliases

    @property
    def chat_ignore_qq_set(self) -> set[str]:
        return {
            part.strip()
            for part in (self.chat_ignore_qq or "").replace("，", ",").split(",")
            if part.strip()
        }

    @property
    def chat_ready(self) -> bool:
        return bool(
            self.chat_enabled
            and self.chat_group_set
            and self.llm_api_key
            and self.llm_base_url
            and self.llm_model
        )

    @property
    def daily_password_group_set(self) -> set[int]:
        """定时推送目标群；未单独配置时沿用聊天白名单群。"""
        return self._parse_ints(self.daily_password_groups) or self.chat_group_set

    @property
    def daily_password_source_list(self) -> list[str]:
        sources: list[str] = []
        for part in (self.daily_password_sources or "").replace("，", ",").split(","):
            name = part.strip().lower()
            if name and name not in sources:
                sources.append(name)
        return sources


@lru_cache
def get_settings() -> Settings:
    return Settings()
