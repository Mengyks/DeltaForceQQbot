# DeltaForceQQbot

为《三角洲行动》玩家提供数据服务的 QQ 群机器人。

**当前状态：v1（卡战备）已完成，126 个自动化测试通过；真实数据源、真实 token 的本机与容器端到端均已验证。**
待办：真实 NapCat + 真实 QQ 群联调（需要你扫码登录），然后提交 Git、部署云服务器。

## 已实现的功能（v1）

命令格式：**`卡战备 <地图> <难度> [兑换]`**（`兑换` 是唯一可选项，含军需处兑换方案）

| 命令 | 说明 |
| --- | --- |
| `卡战备` | 显示可查档位与用法 |
| `卡战备 巴克什 绝密` | 该档位最省的一套方案 + 其他配装思路 |
| `卡战备 航天 绝密 兑换` | 含军需处兑换方案（通常更省，但需要自备兑换券） |
| `卡战备 大坝 机密` / `卡战备 长弓 机密` | 低门槛机密图 |
| `卡战备 航天 机密` / `卡战备 巴克什 机密` | 高门槛机密图 |
| `卡战备 大坝 常规` | 明确告知：常规模式不需要战备值 |

可查档位（门槛来自接口返回的权威值，非人工填写）：

| 地图 | 难度 | 门槛 |
| --- | --- | --- |
| 零号大坝 / 长弓溪谷 | 机密 | 11.25 万 |
| 巴克什 / 航天基地 | 机密 | 18.75 万 |
| 巴克什 | 绝密 | 55 万 |
| 航天基地 | 绝密 | 60 万 |
| 潮汐监狱 | 绝密 | 78 万（另需 8 万入场费） |

**实际回复示例**（真实数据，2026-10-15）：

```
【卡战备 · 巴克什 绝密】门槛 55万 ｜ 数据源 18:40

✅ 最省 · 花费最少-双枪
   总战备 55.01万 ｜ 花费 47.17万 ｜ 比门槛少花 7.83万
   · 枪1 勇士冲锋枪  战备4.18万  花3.8万  假账-9%
   · 枪1-瞄具 3/7可调倍率狙击镜  战备8.82万  花7.34万  假账-16%
   · 枪1-枪管 勇士海狸枪管  战备1.61万  花1.15万  假账-28%
   · 枪2 MP5冲锋枪  战备4.27万  花3.84万  假账-9%
   · 头盔 DAS 防弹头盔 (几乎全新)  战备3.06万  花2.76万  假账-9%

其他配装思路（各取最省一套）：
· 实用优先：花费 48.55万（少花 6.45万）
· 花费最少-单枪：花费 48.91万（少花 6.09万）
· 大胸挂：花费 50.67万（少花 4.33万）

提示：本档位共 35 套候选；想包含军需处兑换加「兑换」。
```

> `假账 -16%` 表示该件装备的战备值比价格高约 16%（越负越划算）。字段语义已用真实响应核对，
> 结论写在 `deltaforce_bot/models/loadout.py` 的模块文档与 `docs/01` 里。

**当前关闭**：`凑战备 <目标值>`（自定义凑战备）。该接口每次请求消耗 10 次数据源资源，成本较高；
代码与测试都保留，`.env` 里把 `CUSTOM_ENABLED=true` 即可恢复。

## 缓存与额度策略

- 卡战备方案**缓存 4 小时**：同一档位 4 小时内的所有查询都直接命中缓存，不调用数据帝。
  依据：已确认战备价格在 4 小时内变化不大（`CACHE_TTL_TIER=14400`）。
- 按 5 个档位估算，额度消耗上限约 **每天 30 次资源**，实际更低（只查热门档位）。
- 三重保护：TTL 缓存 + 同参数并发合并（多人同时查只发一次）+ 每人 2 次/分钟、每群 10 次/分钟限流。
- 缓存是内存级，机器人重启后第一次查询会重新取数（4 小时内其他查询仍免费用缓存）。

## 技术栈

| 项目 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.13 | 与 Docker 镜像 `python:3.13-slim` 对齐 |
| 机器人框架 | NoneBot2 2.5 | 插件化、异步 |
| 协议 | OneBot v11 | 业务代码只依赖 OneBot 标准，协议端可替换 |
| 协议端 | NapCat | Windows 本机易调试，Linux 有 Docker 镜像 |
| 数据来源 | 三角洲数据帝开放平台 | 经适配层接入，见 `docs/02-数据源接口清单.md` |

## 快速开始

```bash
py -3.13 -m venv .venv                                # 需要 Python 3.13
.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env                                  # 填 ORZICE_TOKEN
.venv/Scripts/python.exe -m pytest -q                 # 126 个测试
.venv/Scripts/python.exe bot.py                       # 默认监听 127.0.0.1:8099
```

NapCat 侧配置反向 WebSocket 连接到 `ws://127.0.0.1:8099/onebot/v11/ws`。

## 部署（Docker）

```bash
export MSYS_NO_PATHCONV=1          # Git Bash 用户必加，否则 -v 的容器路径会被改写
docker compose build
docker compose up -d bot           # 只起机器人
docker compose up -d               # 连 NapCat 一起起
# NapCat 首次使用：浏览器打开 http://127.0.0.1:6099 扫码登录 QQ，
# 在「网络配置」新增反向 WebSocket：ws://bot:8099/onebot/v11/ws
docker compose logs -f bot         # 看命令处理与额度消耗
```

要点与已踩过的坑：

- 镜像里 `.env` 不打进去，靠 `env_file` 注入；`config/gates.json` 以只读卷挂载，改档位不必重建镜像。
- **PyPI 访问不稳**：镜像默认使用清华源（`--build-arg PIP_INDEX=...` 可换），否则构建会因 SSL 失败。
- Windows 上 Docker Desktop 需先启动（守护进程不在时 `docker` 命令报 pipe 错误）。
- 端口只绑定回环（`127.0.0.1`），云服务器上不需要对公网开放机器人或 NapCat WebUI。

## 掉线监控

QQ 账号被腾讯判失效时，机器人自身无法通过 QQ 通知你，所以项目内置了连接看护：

- **掉线/被下线**：写日志并推送外部 webhook（若配置了 `ALERT_WEBHOOK_URL`）；
- **恢复上线**：给 `.env` 里 `ALERT_QQ` 指定的管理员 QQ 发私聊，说明离线时长与原因。

配置见 `.env.example` 的「掉线监控」段；实现与测试在 `deltaforce_bot/watchdog.py` 与 `tests/test_watchdog.py`。

## 本地联调工具（不需要 token / 不需要装 NapCat）

| 脚本 | 用途 |
| --- | --- |
| `scripts/mock_api_server.py` | 用 `tests/fixtures/real` 里录制的真实响应冒充数据源 |
| `scripts/fake_onebot_e2e.py` | 冒充 NapCat 发消息、收回复，验证协议链路 |
| `scripts/demo_reply.py` | 直接打印命令的回复文本，用来调排版 |
| `scripts/record_fixture.py` | 录制真实接口响应为测试 fixture（需要 token，会消耗资源） |

完整联调（三个终端）：

```bash
export MSYS_NO_PATHCONV=1   # 传 /v1/... 这类参数时必须加
.venv/Scripts/python.exe scripts/mock_api_server.py --port 8800
ORZICE_BASE_URL=http://127.0.0.1:8800 ORZICE_TOKEN=test-token PORT=8100 .venv/Scripts/python.exe bot.py
.venv/Scripts/python.exe scripts/fake_onebot_e2e.py --uri ws://127.0.0.1:8100/onebot/v11/ws "卡战备 巴克什 绝密"
```

## 目录结构

```
DeltaForceQQbot/
├── bot.py                        # 启动入口（含日志接管与关闭释放）
├── config/gates.json             # 地图 + 难度 → 接口档位（改配置不改代码）
├── deltaforce_bot/
│   ├── config.py                 # .env → Settings
│   ├── logging_setup.py          # 把标准库 logging 接到 loguru（NoneBot 2.5 不再自动接管）
│   ├── runtime.py                # 运行时装配（客户端/服务/限流器）
│   ├── adapters/                 # orzice 客户端、TTL 缓存、并发合并、额度、限流
│   ├── models/                   # 数据模型（字段语义见 loadout.py 文档）与入口解析
│   └── services/                 # gear_loadout（业务）、commands（编排）、text（解析与排版）
├── plugins/gear_loadout.py       # NoneBot 插件入口（只做收发）
├── scripts/                      # 联调与运维脚本
├── tests/                        # 126 个用例，含真实响应契约测试
└── docs/                         # 规划与设计文档
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/00-项目规划.md](docs/00-项目规划.md) | 目标、服务路线图（v2 每日密码 / v3 制造台利润 / v4 主播开播监听）、架构、里程碑、风险 |
| [docs/01-卡战备-功能设计.md](docs/01-卡战备-功能设计.md) | 游戏机制与**已核对的字段语义**、命令设计、验证与验收 |
| [docs/02-数据源接口清单.md](docs/02-数据源接口清单.md) | 接口规格、真实门槛与分组结构、成本与缓存策略 |
| [docs/03-部署与联调.md](docs/03-部署与联调.md) | 服务器部署（含 NapCat 原生安装 + systemd）、扫码登录、反向 WS 配置、运维与本机踩坑记录 |

## 开发流程约定

1. 讨论确定需求 → 2. 细化实现方案 → 3. 编码 → 4. 本机测试
5. **每次改动先交用户审阅**，确认后才提交 Git（版本号按里程碑递增）
6. 本机验收通过 → Docker 打包 → Linux 云服务器上线

## 免责声明

本项目为非官方第三方工具，与腾讯及《三角洲行动》官方无关。数据来自社区第三方平台；QQ 接入使用第三方协议端（NapCat），存在账号风控风险，建议使用小号并控制调用频率。项目不提供任何自动化游戏操作类功能。请遵守游戏与平台的相关服务条款。
