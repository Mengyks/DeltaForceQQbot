"""数据源相关的异常类型。

业务层通过这些异常区分"该提示用户什么"，不直接暴露第三方错误细节。
"""

from __future__ import annotations


class DataSourceError(Exception):
    """数据源不可用、返回异常或格式不符。"""


class DataSourceAuthError(DataSourceError):
    """token 缺失、无效或无权访问该接口。"""


class QuotaExhausted(DataSourceError):
    """本地额度熔断：当日资源次数已达上限，主动停止调用。"""
