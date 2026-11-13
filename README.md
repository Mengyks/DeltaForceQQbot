# DeltaForceQQbot

> **接手开发 / 新会话请先读 [docs/19-当前交接文档.md](docs/19-当前交接文档.md)**（当前功能/机制/架构/配置/部署/已知问题），
> 掉线恢复看 [docs/13-掉线重启指南.md](docs/13-掉线重启指南.md)；群白名单/聊天开关看
> [docs/15-群聊白名单与聊天开关.md](docs/15-群聊白名单与聊天开关.md)；各功能细节看
> [docs/10-直播订阅.md](docs/10-直播订阅.md)、[docs/11-制造台利润.md](docs/11-制造台利润.md)、
> [docs/12-帮助命令.md](docs/12-帮助命令.md)、[docs/08-每日密码.md](docs/08-每日密码.md)、
> [docs/16-猪猪表情包.md](docs/16-猪猪表情包.md)、[docs/17-猪结婚.md](docs/17-猪结婚.md)、
> [docs/18-每日天尊挑战.md](docs/18-每日天尊挑战.md)，
> 历史背景看 [docs/05-V1交接总结.md](docs/05-V1交接总结.md)、[docs/07-V2群聊机器人交接总结.md](docs/07-V2群聊机器人交接总结.md)、[docs/09-V3交接总结.md](docs/09-V3交接总结.md)、[docs/14-V6交接总结.md](docs/14-V6交接总结.md)。

为《三角洲行动》玩家提供数据服务的 QQ 群机器人。

**当前状态：v0.9.0（帮助页，已部署待验收）** —— v1 卡战备、v2 群聊机器人、v3 每日密码、v4 B站直播订阅、v5 制造台利润均已上线；
v6 帮助页已完成：`/帮助` 返回五点老师的功能指令与示例图片卡片；
另有 v0.9.0 之后的增量功能「猪猪表情包」（群友发 `猪`/`zhu`/`pig`/`🐖`/`🐷` 随机回图，独立于聊天）、
「猪结婚」（`/waifu` 或 `/每天日尊挑战`，每天每群形成不重叠配对并拼图回复）
和「每日天尊挑战」（`/每日天尊挑战`，随机怪物 + 武器 + 头像生成三栏图）；
398 个自动化测试全部通过；
线上已运行 v0.9.0（帮助页），等待群内验收；
代码已提交 GitHub `Mengyks/DeltaForceQQbot`，tag `v0.9.0`。
v4 直播见 [docs/10](docs/10-直播订阅.md)，v5 制造台利润见 [docs/11](docs/11-制造台利润.md)，v6 帮助页见 [docs/12](docs/12-帮助命令.md)，
猪猪表情包见 [docs/16](docs/16-猪猪表情包.md)，猪结婚见 [docs/17](docs/17-猪结婚.md)，每日天尊挑战见 [docs/18](docs/18-每日天尊挑战.md)。
v3 每日密码：每天 0:15 预热当天缓存、11:00 推送到白名单群，群友也可发 `/每日密码` 查询；
使用免费的 4399 游戏盒 / eleven.icu 接口，不消耗数据帝额度，见 [docs/08](docs/08-每日密码.md)。
v4 直播订阅见 [docs/10](docs/10-直播订阅.md)。

## 已实现的功能（v1）

命令格式：**`/卡战备 <地图> <难度> [兑换]`**（`兑换` 是唯一可选项，含军需处兑换方案）

| 命令 | 说明 |
| --- | --- |
| `/卡战备` | 显示可查档位与用法 |
| `/卡战备 巴克什 绝密` | 该档位最省的一套方案 + 其他配装思路 |
| `/卡战备 航天 绝密 兑换` | 含军需处兑换方案（通常更省，但需要自备兑换券） |
| `/卡战备 大坝 机密` / `/卡战备 长弓 机密` | 低门槛机密图 |
| `/卡战备 航天 机密` / `/卡战备 巴克什 机密` | 高门槛机密图 |
| `/卡战备 大坝 常规` | 明确告知：常规模式不需要战备值 |

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

**当前关闭**：`/凑战备 <目标值>`（自定义凑战备）。该接口每次请求消耗 10 次数据源资源，成本较高；
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
.venv/Scripts/python.exe -m pytest -q                 # 398 个测试
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
| `--upload-env` | 用本地 `.env` 覆盖服务器配置（默认**保留服务器 .env**） |
| `--keep-env` | 兼容旧参数；现在默认就保留服务器 `.env` |
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

## 每日密码（v3）

群里发 **`/每日密码`** 查询当天地图密码；每天北京时间 **0:15 预热缓存、11:00 自动推送**到白名单群。
使用免费接口，不消耗数据帝额度。

```dotenv
DAILY_PASSWORD_ENABLED=true
DAILY_PASSWORD_SOURCES=4399,eleven     # 主源 4399 游戏盒，失败换 eleven.icu
DAILY_PASSWORD_REFRESH_TIME=00:15      # 预热当天缓存
DAILY_PASSWORD_PUSH_TIME=11:00         # 固定推送
DAILY_PASSWORD_GROUPS=                 # 为空则沿用 CHAT_GROUPS
DAILY_PASSWORD_QUERY_COOLDOWN=300      # 同一个群 5 分钟一次
```

- 0:15 预热成功后，白天的手动查询和 11:00 推送都直接使用这份缓存；同一天只向上游查询一次。
- 快照写入独立 `data/daily_password.sqlite3`；0:15 预热失败会每 60 秒重试，最多 15 分钟。
- 11:00 后 60 分钟内可补推；同一天每个群只成功推送一次，失败会重试。
- 返回图片卡片（六地图背景 + 大号密码，约 217 KB JPEG）；渲染或发图失败自动回退文字。
- 与聊天互不干扰：`/每日密码` 不会被聊天机器人当成聊天内容；卡战备不受影响。
- 图片卡片预览：`.venv/Scripts/python.exe scripts/render_daily_password_preview.py`
  → `assets/_preview/daily_password_preview.jpg`。
- 完整接口实测、配置与验收见 [docs/08-每日密码.md](docs/08-每日密码.md)。

## 直播订阅（v4，B站）

订阅 B 站主播 UID；主播开播时，机器人把**开播卡片 + 文字直播间链接**推送到两个群。
只做开播提醒，不做下播提醒；接口免费、无需登录。

```dotenv
LIVE_ENABLED=true
LIVE_DB_PATH=data/live.sqlite3
LIVE_POLL_INTERVAL=180          # 每 3 分钟批量查一次
LIVE_IMAGE_MAX_KB=350           # 卡片压缩目标；超过自动降质量
```

```
/直播订阅 添加 <B站UID>
/直播订阅 删除 <B站UID>
/直播订阅 列表
/直播订阅 查询 <B站UID>
```

- **全局订阅**：任意一个群添加/删除，两个群同时生效；开播时同时推两个群。
- **权限**：只有群主和 `SUPERUSERS` 可以添加/删除；普通群友可以看列表和查询。
- **添加时正在直播**：只回复“当前正在直播”，抑制当前场次；下次真正开播再推。
- **去重**：用 B 站返回的 `live_time` 作为场次 ID；同一场每个群只推一次，服务重启后也不会重复。
- **轮询**：默认 180 秒；批量查询所有订阅。`live_status=1` 才算直播中，`0`/`2`（轮播）不推。
- **推送**：HTML + Playwright 渲染开播卡片（主播名 -- 直播中、封面、直播间地址），
  同一消息内附文字直播间链接；实测约 58 KB，超过 350 KB 会自动降低 JPEG 质量。
  卡片或图片发送失败时回退纯文字，文字里始终有直播间链接。
- **不影响其他功能**：独立 `data/live.sqlite3`；接口失败只退避重试，不会误判下播。
- **关键坑**：B 站会拦截 `python-httpx` 默认 UA（HTTP 412），客户端固定使用浏览器 UA + Referer；
  批量参数必须写成 `uids[]=...`，不能写成 `uids=1,2`。
- 卡片预览：`.venv/Scripts/python.exe scripts/render_live_preview.py`
  → `assets/_preview/live_subscription_preview.jpg`。
- 完整接口实测、配置与验收见 [docs/10-直播订阅.md](docs/10-直播订阅.md)。

## 制造台利润（v5）

群里发 **`/制造利润`**（也支持 `/制造台利润`、`/每日利润`），返回四个制造台 Lv3
当前每小时利润最高的物品卡片。

- 四个台：技术中心、工作台、制药台、防具台；每台只取 `price_hour` 最高的一条。
- 只展示单次利润和每小时利润；物品图使用等级边框色（1 灰 / 2 绿 / 3 蓝 / 4 紫 / 5 金 / 6 红）。
- 标题下方显示**查询时间**（这批数据的实际获取时间；缓存复用保持不变，方便判断数据新鲜度）。
- 数据源：数据帝 Pro `manufacturePro`，固定 Lv3；每个台一次请求，一张卡片最多 4 次资源。
- **按需查询 + 12 小时缓存**：缓存期内重复查询不再请求上游；没有定时任务。
- 图片资源：技术中心 `accessories`/`weapon`，工作台 `bullet`，制药台 `supplies`，
  防具台 `helmets`/`backpacks`/`armor`/`chest_rigs`；按 `grade` 进等级目录，名称宽松匹配。
- 单台获取失败时卡片显示「数据获取失败」；渲染/发图失败自动回退文字。

```dotenv
CRAFTING_ENABLED=true
CRAFTING_CACHE_TTL=43200      # 12 小时
CRAFTING_IMAGE_MAX_KB=350
```

- 卡片预览：`.venv/Scripts/python.exe scripts/render_crafting_preview.py`
  → `assets/_preview/crafting_profit_preview.jpg`。
- 接口字段口径、图片映射与验收见 [docs/11-制造台利润.md](docs/11-制造台利润.md)。

## 帮助（v6）

群里发 **`/帮助`**（也支持 `/帮助命令`、`/help`），返回五点老师的功能指令与示例图片卡片。

- 标题：`五点老师 · 帮助命令`
- 背景：`assets/items/backgrounds/帮助窗口背景.jpg`
- 内容：卡战备、每日密码、制造利润、直播订阅、群聊、管理员六块，每块有指令格式 + 示例
- 卡片渲染成功后缓存 24 小时；渲染/发图失败回退同内容的文字版
- 不请求任何外部接口，不消耗数据帝资源
- 预览：`.venv/Scripts/python.exe scripts/render_help_preview.py`
  → `assets/_preview/help_preview.jpg`

## 猪猪表情包（v0.9.0 后增量）

群里发 **`猪`**、**`zhu`**、**`pig`**、**`🐖`**、**`🐷`** 时，机器人从本地目录随机回一张猪表情包。

- 只认**精确**触发词：`猪猪`、`小猪`、`猪！` 不触发；`@机器人 猪` 会触发。
- **按群隔离**：只回复触发消息所在的群，不会向白名单群广播；同群 5 秒冷却，别的群不受影响。
- 独立功能：不依赖 `CHAT_ENABLED`/`CHAT_GROUPS`，但必须在 `FUN_GROUPS` 白名单内；命中后也不会交给 LLM 聊天。
- 图片目录 `assets/memes/pig`（服务器同名路径），启动时扫描；当前 100 张静态图，约 3.4 MB。
- 图片不入 Git，部署/换机需要单独上传。

```dotenv
FUN_GROUPS=1071781482,1130331921
PIG_MEME_ENABLED=true
PIG_MEME_DIR=assets/memes/pig
PIG_MEME_COOLDOWN=5
```

完整说明、素材来源与验收步骤见 [docs/16-猪猪表情包.md](docs/16-猪猪表情包.md)。

## 猪结婚（v0.9.0 后增量）

群里发 **`/waifu`** 或 **`/每天日尊挑战`**，机器人把发送者头像和随机选中的群友头像
拼进「猪结婚」模板，以引用回复的形式发回，并附带文字「你今天的老婆是 xxx」。

- **每天每群形成互不重叠的配对**：A 选中 B 后，A/B 都被锁定；B 再发直接返回 A，
  其他人不能选 A/B。
- A、B 共享同一张配对图（A 左 B 右），文字按发送者换成对方群昵称。
- 群昵称优先群名片，其次 QQ 昵称，最后 QQ 号；排除机器人。
- 配对按“日期 + 群号”存 SQLite `data/waifu.sqlite3`，跨天重置，重启不丢；只保留今天和昨天。
- 独立功能：不依赖 `CHAT_ENABLED`/`CHAT_GROUPS`，但必须在 `FUN_GROUPS` 白名单内。
- 依赖 Pillow 拼图；模板 `assets/memes/waifu/猪结婚.jpg` 不入 Git，部署需要单独上传。

```dotenv
FUN_GROUPS=1071781482,1130331921
WAIFU_ENABLED=true
WAIFU_DB_PATH=data/waifu.sqlite3
WAIFU_TEMPLATE=assets/memes/waifu/猪结婚.jpg
WAIFU_KEEP_DAYS=2
```

完整规则、模板坐标与部署步骤见 [docs/17-猪结婚.md](docs/17-猪结婚.md)。

## 每日天尊挑战（v0.9.0 后增量）

群里发 **`/每日天尊挑战`**，机器人随机抽一个怪物、一种武器，取发送者头像，
按「今日天尊 / 今日猎物 / 今日武器」三栏生成 HTML，用 Playwright 截图并压缩，
以 QQ 引用回复的形式只发图片。

- 素材：`assets/monsters`（71 张）、`assets/weapons`（14 种）、`assets/monster_background`。
- 文件名格式 `序号、名称`，索引时只取「、」后面的名称。
- 按“日期 + 群号 + 群友”缓存一天，同一人当天重复查询结果不变；不同群/不同人各自一份。
- 图片包含发送者的群昵称；群名片为空时退回 QQ 昵称/QQ 号。
- 预览图 1600×900，JPEG 质量阶梯压缩，目标 350 KB。
- 与 `/waifu` 没有数据交集：独立插件、独立 SQLite `data/daily_challenge.sqlite3`。
- 独立于聊天，但必须在 `FUN_GROUPS` 白名单内。

```dotenv
FUN_GROUPS=1071781482,1130331921
DAILY_CHALLENGE_ENABLED=true
DAILY_CHALLENGE_DB_PATH=data/daily_challenge.sqlite3
DAILY_CHALLENGE_IMAGE_MAX_KB=350
```

完整素材说明、模板与部署步骤见 [docs/18-每日天尊挑战.md](docs/18-每日天尊挑战.md)。

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
.venv/Scripts/python.exe scripts/fake_onebot_e2e.py --uri ws://127.0.0.1:8100/onebot/v11/ws "/卡战备 巴克什 绝密"
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
│   ├── adapters/                 # orzice/LLM/头像客户端、图库索引、SQLite、表情包、缓存、限流
│   ├── models/                   # 数据模型（字段语义见 loadout.py / crafting.py 文档）
│   ├── render/                   # Jinja2 + Playwright 卡片；Pillow 猪结婚拼图；天尊挑战 HTML
│   └── services/                 # gear_loadout、chat、daily_password、live_watch、crafting、pig_meme、waifu、daily_challenge、commands、text
├── plugins/gear_loadout.py       # 卡战备入口
├── plugins/llm_chat.py           # 群聊机器人入口（记录器/聊天/清理指令）
├── plugins/daily_password.py     # 每日密码入口（查询 + 0:15 预热 + 11:00 推送）
├── plugins/live_watch.py         # B站直播订阅入口（命令 + 180s 轮询 + 卡片推送）
├── plugins/crafting.py           # 制造台利润入口（/制造利润 + 卡片）
├── plugins/help.py               # 帮助入口（/帮助 + 背景卡片）
├── plugins/pig_meme.py           # 猪猪表情包入口（精确触发词 + 按群冷却）
├── plugins/waifu.py              # 猪结婚入口（配对 + 拼图 + 引用回复）
├── plugins/daily_challenge.py    # 每日天尊挑战入口（图库 + HTML 截图）
├── scripts/                      # 联调与运维脚本
├── tests/                        # 398 个用例，含真实响应契约测试
└── docs/                         # 规划与设计文档
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/00-项目规划.md](docs/00-项目规划.md) | 目标、服务路线图、架构、里程碑、风险 |
| [docs/01-卡战备-功能设计.md](docs/01-卡战备-功能设计.md) | 游戏机制与**已核对的字段语义**、命令设计、验证与验收 |
| [docs/02-数据源接口清单.md](docs/02-数据源接口清单.md) | 接口规格、真实门槛与分组结构、成本与缓存策略 |
| [docs/03-部署与联调.md](docs/03-部署与联调.md) | 服务器部署（含 NapCat 原生安装 + systemd）、扫码登录、反向 WS 配置、运维与本机踩坑记录 |
| [docs/04-卡片渲染设计.md](docs/04-卡片渲染设计.md) | 卡片渲染：模板结构、图片目录映射、等级配色、缓存与降级 |
| [docs/05-V1交接总结.md](docs/05-V1交接总结.md) | **V1 交接总结**：现状、关键事实、部署、流程约定、v2 起点 |
| [docs/06-LLM群聊机器人.md](docs/06-LLM群聊机器人.md) | v2 群聊机器人：触发、上下文、图片、权限、配置与验收 |
| [docs/07-V2群聊机器人交接总结.md](docs/07-V2群聊机器人交接总结.md) | **V2 历史交接**：聊天/表情包/人格、部署运维、每日密码接入点 |
| [docs/08-每日密码.md](docs/08-每日密码.md) | **V3 每日密码**：免费接口实测、缓存/推送规则、配置、部署与验收 |
| [docs/09-V3交接总结.md](docs/09-V3交接总结.md) | **V3 功能交接与下一版规划**：当前功能/生产配置、已知问题、直播订阅与制作台利润接入建议 |
| [docs/10-直播订阅.md](docs/10-直播订阅.md) | **V4 B站直播订阅**：接口实测（UA/412）、全局订阅、轮询去重、卡片与压缩、配置与验收 |
| [docs/11-制造台利润.md](docs/11-制造台利润.md) | **V5 制造台利润**：Pro 接口字段口径、四台 Lv3、宽松图片索引、12h 缓存、卡片与费用 |
| [docs/12-帮助命令.md](docs/12-帮助命令.md) | **V6 帮助页**：`/帮助` 图片卡片、功能指令与示例、帮助窗口背景、缓存与文字兜底 |
| [docs/13-掉线重启指南.md](docs/13-掉线重启指南.md) | **运维**：QQ 被踢/NapCat 掉线/服务重启的判断、扫码登录与恢复步骤 |
| [docs/14-V6交接总结.md](docs/14-V6交接总结.md) | **当前交接文档**：v0.9.0 全部功能、架构、配置参数、存储、部署、已知问题与清单 |
| [docs/15-群聊白名单与聊天开关.md](docs/15-群聊白名单与聊天开关.md) | **运维**：把群加入/移出聊天白名单、全局开关聊天、对其他功能的影响 |
| [docs/16-猪猪表情包.md](docs/16-猪猪表情包.md) | **猪猪表情包**：触发词、按群隔离与冷却、素材来源、部署与验收 |
| [docs/17-猪结婚.md](docs/17-猪结婚.md) | **猪结婚**：每日不重叠配对、共享缓存、拼图坐标、SQLite 与验收 |
| [docs/18-每日天尊挑战.md](docs/18-每日天尊挑战.md) | **每日天尊挑战**：图库命名、三栏 HTML、Playwright 截图压缩、缓存与白名单 |
| [docs/19-当前交接文档.md](docs/19-当前交接文档.md) | **当前交接文档**：全部功能、实现机制、架构、配置、存储、部署与已知问题 |

## 开发流程约定

1. 讨论确定需求 → 2. 细化实现方案 → 3. 编码 → 4. 本机测试
5. **每次改动先交用户审阅**，确认后才提交 Git（版本号按里程碑递增）
6. 本机验收通过 → 原生部署到 Linux 云服务器上线

## 免责声明

本项目为非官方第三方工具，与腾讯及《三角洲行动》官方无关。数据来自社区第三方平台；QQ 接入使用第三方协议端（NapCat），存在账号风控风险，建议使用小号并控制调用频率。项目不提供任何自动化游戏操作类功能。请遵守游戏与平台的相关服务条款。
