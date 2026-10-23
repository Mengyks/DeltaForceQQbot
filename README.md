# DeltaForceQQbot

> **接手开发 / 新会话请先读 [docs/05-V1交接总结.md](docs/05-V1交接总结.md)**：V1 的全部结论、数据源字段语义、服务器部署方式、踩过的坑与 v2 起点都在里面。

为《三角洲行动》玩家提供数据服务的 QQ 群机器人。

**当前状态：v0.5.2** —— v1 卡战备与 v2 群聊机器人均已上线；卡战备以**图片卡片**形式在真实 QQ 群服务中，
196 个自动化测试通过；代码已推送 GitHub（tag `v0.5.2`），线上运行在腾讯云 `39.106.135.148`（bot 原生 systemd + NapCat 原生）。
下一步：观察本地表情包接梗效果并调整分类/频率（见 [docs/06](docs/06-LLM群聊机器人.md)）。

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
| 语言 | Python 3.13 | 原生部署与本地开发统一使用 Python 3.13 |
| 机器人框架 | NoneBot2 2.5 | 插件化、异步 |
| 协议 | OneBot v11 | 业务代码只依赖 OneBot 标准，协议端可替换 |
| 协议端 | NapCat | Windows 本机易调试；Linux 生产用原生安装 + systemd |
| 数据来源 | 三角洲数据帝开放平台 | 经适配层接入，见 `docs/02-数据源接口清单.md` |

## 快速开始

```bash
py -3.13 -m venv .venv                                # 需要 Python 3.13
.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env                                  # 填 ORZICE_TOKEN
.venv/Scripts/python.exe -m pytest -q                 # 196 个测试
.venv/Scripts/python.exe bot.py                       # 默认监听 127.0.0.1:8099
```

NapCat 侧配置反向 WebSocket 连接到 `ws://127.0.0.1:8099/onebot/v11/ws`。

## 部署（原生 systemd）

生产环境**不再构建或传输 Docker 镜像**：服务器用 venv + systemd 直接运行 `bot.py`，
NapCat 同样是原生 systemd，两者通过 `127.0.0.1:8099` 通信。Docker Compose 只保留给本地开发/联调。

首次部署或更新代码：

```bash
export MSYS_NO_PATHCONV=1          # Git Bash 用户必加

# 默认：上传源码 → 服务器引导（venv/依赖/Chromium/字体）→ 安装并重启 systemd 服务
.venv/Scripts/python.exe scripts/deploy_server.py --host 39.106.135.148 --user root

# 图片没变时跳过 56MB 的 assets/
.venv/Scripts/python.exe scripts/deploy_server.py --host 39.106.135.148 --user root --skip-assets
```

脚本会做这些事：

- SFTP 上传项目源码与 `.env` 到 `/opt/deltaforce-qqbot`；
- 创建专用系统用户 `deltaforce`（可用 `--service-user` 改）；
- 校验 Python >= 3.13，创建/更新 `.venv`，按 `pyproject.toml` 哈希安装 `.[render]` 依赖；
- 安装 Playwright Chromium、Chromium 系统依赖和文泉驿中文字体；
- 写入 `/etc/systemd/system/deltaforce-bot.service`（模板见 `deploy/deltaforce-bot.service`），
  `systemctl enable` 并重启服务；最后检查进程是否保持运行。

常用参数：

| 参数 | 用途 |
| --- | --- |
| `--skip-assets` | 跳过 `assets/`（图片资源 56MB，只在换图时需要传） |
| `--exclude-tests` | 不上传 `tests/` |
| `--keep-env` | 不覆盖服务器上已有的 `.env` |
| `--skip-deps` | 只更新代码，跳过 pip 与浏览器安装 |
| `--force-bootstrap` | 强制重装 Python 依赖与 Chromium 系统依赖 |
| `--no-restart` | 完成引导和单元安装，但不重启服务 |
| `--no-up` / `--no-upload` | 只上传 / 只引导重启 |
| `--stop-old-docker` | 首次从 Docker 迁移时，停止旧的 `deltaforce-bot` 容器（保留容器便于回退） |
| `--playwright-download-host` | 浏览器下载镜像（国内网络可用 `https://npmmirror.com/mirrors/playwright`） |
| `--docker` | 使用旧的 docker compose 部署路径（仅回退/对照） |

运维命令：

```bash
# 看机器人日志（含额度消耗）
ssh root@39.106.135.148 'journalctl -u deltaforce-bot -f'

# 重启 / 状态
ssh root@39.106.135.148 'systemctl restart deltaforce-bot'
ssh root@39.106.135.148 'systemctl status deltaforce-bot'

# 不想开新 SSH 时，用部署脚本执行
.venv/Scripts/python.exe scripts/deploy_server.py --host 39.106.135.148 --user root \
  --cmd "journalctl -u deltaforce-bot -n 50 --no-pager"
```

要点与已踩过的坑：

- `.env` 通过 systemd 的 `EnvironmentFile` 注入，包含 `ORZICE_TOKEN`、`DRIVER`、`SUPERUSERS` 等；
  服务器上权限为 `root:deltaforce 640`。
- 服务只监听 `127.0.0.1:8099`，NapCat 的反向 WebSocket 地址不变：`ws://127.0.0.1:8099/onebot/v11/ws`。
- 渲染使用 Playwright 自带的 Chromium（不是 Ubuntu apt 的 snap 包）；`PLAYWRIGHT_BROWSERS_PATH`
  固定在 `/opt/deltaforce-qqbot/.playwright`，服务用户可读。
- 首次引导需要联网安装依赖和浏览器；脚本是幂等的，SSH 断开后重新执行即可。
- `data/` 预留给后续 SQLite 等持久化数据，归服务用户所有。
- 旧 Docker 方式仍可用 `--docker` 部署，但生产不再依赖它；服务器不需要访问 Docker Hub。

## 掉线监控

QQ 账号被腾讯判失效时，机器人自身无法通过 QQ 通知你，所以项目内置了连接看护：

- **掉线/被下线**：写日志并推送外部 webhook（若配置了 `ALERT_WEBHOOK_URL`）；
- **恢复上线**：给 `.env` 里 `ALERT_QQ` 指定的管理员 QQ 发私聊，说明离线时长与原因。

配置见 `.env.example` 的「掉线监控」段；实现与测试在 `deltaforce_bot/watchdog.py` 与 `tests/test_watchdog.py`。

## 群聊机器人（v2）

在指定群聊里接入 DeepSeek V4.1 Flash（OpenAI 兼容接口），支持群共享上下文和图片/表情包理解。
默认关闭，配置 `.env` 后启用；只处理群聊，不开私聊。

```dotenv
CHAT_ENABLED=true
CHAT_GROUPS=群号1,群号2
CHAT_ALIASES=机器人别名1,别名2
SUPERUSERS=["你的QQ号"]          # 只有这里的人能清理上下文

LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的key
LLM_MODEL=deepseek-flash

CHAT_MEME_ENABLED=true
CHAT_MEME_DIR=assets/memes
```

- 被 @ / 引用机器人 / 叫名字立即回复；否则在机器人上次回复后的 180 秒跟进窗口内继续参与，
  窗口内最多 3 次、间隔至少 20 秒。
- “主动参与”默认关闭；想让它不 @ 也偶尔接话时，把 `CHAT_AMBIENT_ENABLED=true`（默认间隔 30 秒）。
- 聊天直接发纯文本，不使用 QQ 的引用回复；模型输出的多句短句会拆成最多 3 条独立消息逐条发送。
- 当前消息若是在回复别人，会把被回复内容带进上下文。
- 本地表情包：模型可通过 `send_meme(category)` 从 `assets/memes` 选一张发出来；
  同群 30 秒冷却、10 分钟最多 5 张，避开最近发过的图。不做网页图片搜索。
- 群消息写入 `data/chat.sqlite3` 作为群共享上下文，默认保留 7 天。
- `@bot 上下文清理 提取摘要` / `@bot 上下文清理 直接清理` 仅 `SUPERUSERS` 可用。
- 人设写 `config/persona.md`，修改后自动热加载。
- 没有新增依赖；配置和完整说明见 [docs/06-LLM群聊机器人.md](docs/06-LLM群聊机器人.md)。

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
├── config/persona.md             # 群聊机器人人设（可自行改写，自动热加载）
├── deploy/deltaforce-bot.service # 原生 systemd 单元模板
├── deltaforce_bot/
│   ├── config.py                 # .env → Settings
│   ├── logging_setup.py          # 把标准库 logging 接到 loguru（NoneBot 2.5 不再自动接管）
│   ├── runtime.py                # 运行时装配（客户端/服务/限流器）
│   ├── adapters/                 # orzice/LLM 客户端、SQLite、本地表情包、TTL 缓存、限流
│   ├── models/                   # 数据模型（字段语义见 loadout.py 文档）与入口解析
│   └── services/                 # gear_loadout、chat、commands、text
├── plugins/gear_loadout.py       # 卡战备入口
├── plugins/llm_chat.py           # 群聊机器人入口（记录器/聊天/清理指令）
├── scripts/                      # 联调与运维脚本
├── tests/                        # 196 个用例，含真实响应契约测试
└── docs/                         # 规划与设计文档
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/00-项目规划.md](docs/00-项目规划.md) | 目标、服务路线图（v2 群聊机器人 / v3 制造台利润 / v4 主播开播监听）、架构、里程碑、风险 |
| [docs/01-卡战备-功能设计.md](docs/01-卡战备-功能设计.md) | 游戏机制与**已核对的字段语义**、命令设计、验证与验收 |
| [docs/02-数据源接口清单.md](docs/02-数据源接口清单.md) | 接口规格、真实门槛与分组结构、成本与缓存策略 |
| [docs/03-部署与联调.md](docs/03-部署与联调.md) | 服务器部署（含 NapCat 原生安装 + systemd）、扫码登录、反向 WS 配置、运维与本机踩坑记录 |
| [docs/04-卡片渲染设计.md](docs/04-卡片渲染设计.md) | 卡片渲染：模板结构、图片目录映射、等级配色、缓存与降级 |
| [docs/05-V1交接总结.md](docs/05-V1交接总结.md) | **V1 交接总结（新会话先读）**：现状、关键事实、部署、流程约定、v2 起点 |
| [docs/06-LLM群聊机器人.md](docs/06-LLM群聊机器人.md) | v2 群聊机器人：触发、上下文、图片、权限、配置与验收 |

## 开发流程约定

1. 讨论确定需求 → 2. 细化实现方案 → 3. 编码 → 4. 本机测试
5. **每次改动先交用户审阅**，确认后才提交 Git（版本号按里程碑递增）
6. 本机验收通过 → 原生部署到 Linux 云服务器上线

## 免责声明

本项目为非官方第三方工具，与腾讯及《三角洲行动》官方无关。数据来自社区第三方平台；QQ 接入使用第三方协议端（NapCat），存在账号风控风险，建议使用小号并控制调用频率。项目不提供任何自动化游戏操作类功能。请遵守游戏与平台的相关服务条款。
