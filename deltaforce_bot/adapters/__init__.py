"""数据源接入层：第三方 API 客户端与配套的缓存、限流、额度控制。

本层不包含业务逻辑，只负责"把外部数据安全、经济地取回来"：
- token 只在此层注入请求，绝不写入日志或缓存键
- 所有请求强制走缓存与并发合并，避免重复消耗平台额度
- 统一异常类型，业务层只需处理 DataSourceError / DataSourceAuthError / QuotaExhausted
"""

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted

__all__ = ["DataSourceError", "DataSourceAuthError", "QuotaExhausted"]
