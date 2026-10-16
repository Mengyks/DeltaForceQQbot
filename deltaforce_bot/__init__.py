"""三角洲行动 QQ 机器人业务包。

分层约定：
- ``adapters``：数据源接入（第三方 API 客户端、缓存、限流、额度），不含业务逻辑
- ``models``：数据模型（pydantic），负责把第三方返回的原始数据转成稳定结构
- ``services``：业务逻辑（卡战备等），不依赖 NoneBot，可独立单测
- ``plugins``：NoneBot 插件目录（在项目根的 ``plugins/``），只做命令解析与消息收发
"""

__version__ = "0.1.0"
