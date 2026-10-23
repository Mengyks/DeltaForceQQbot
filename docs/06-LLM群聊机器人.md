# 群聊机器人（v2）

> 状态：已实现，默认关闭。填好 `.env` 的 `CHAT_*` / `LLM_*` 并重启服务后生效。
> 只影响群聊，不改卡战备逻辑；`CHAT_ENABLED=false` 可随时关闭。

## 1. 交互规则

- **只开群聊**：只处理 `GroupMessageEvent`；私聊不进入聊天。
- **群白名单**：`CHAT_GROUPS`；为空或 `CHAT_ENABLED=false` 时聊天完全关闭。
- **立即触发**：任意位置的 @机器人、引用机器人消息、文本包含 `CHAT_ALIASES` 任一项。
- **跟进窗口**：机器人成功回复后开 `CHAT_FOLLOWUP_WINDOW=180` 秒窗口；窗口内所有人类群消息都可以触发，最多再回 `CHAT_FOLLOWUP_MAX_REPLIES=3` 次，两次至少间隔 `CHAT_COOLDOWN=20` 秒。
- **主动参与（ambient）**：默认关闭。设为 `CHAT_AMBIENT_ENABLED=true` 后，即使没有 @、也没有窗口，只要距离机器人在该群上次回复超过 `CHAT_AMBIENT_COOLDOWN=30` 秒，任何正常长度的群消息都可以触发它接话；太短的消息（默认少于 2 个字，如“6”“嗯”）不会触发，图片消息可以。
- **窗口按群独立**：在 A 群 @ 开窗不会让 B 群自动跟进；B 群靠 ambient 模式参与。
- **忽略**：`卡战备`/`凑战备`/`上下文清理` 命令、机器人自己、`CHAT_IGNORE_QQ` 里的账号、空消息。
- **群共享上下文**：所有白名单群的人类消息会写入 SQLite，供下一次 @/跟进回复时参考；不是只记录被 @ 的消息。
- **图片/表情包**：当前消息里的 `image`/`mface` 会尽量下载并作为 `image_url` 发给模型；下载失败降级成文字 `[图片]`。历史消息只保存 `[图片]` 占位，不保存图片字节。
- **长回复**：按段落切成最多 3 段，直接用纯文本发送，不使用 QQ 的“引用回复”功能；发送成功后才写入 assistant 消息并开窗口。
- **接梗上下文**：如果当前消息是在回复别人的某句话，会把被回复的文本一起带进模型上下文，减少答非所问。

## 2. 指令

| 指令 | 权限 | 行为 |
| --- | --- | --- |
| `@机器人 上下文清理 提取摘要` | 仅 `SUPERUSERS` | 先让模型压缩当前群上下文；成功写摘要并删除原始消息；失败不删任何数据 |
| `@机器人 上下文清理 直接清理` | 仅 `SUPERUSERS` | 删除该群全部聊天记录和摘要 |

- 群主、群管理员**没有**权限；只有 `.env` 的 `SUPERUSERS` 可以。
- 非超级用户执行会收到“无权限”，不会产生任何清理。
- 清理只影响聊天上下文，不影响卡战备缓存/额度。

## 3. 配置

本地 `.env`：`D:\Project\DeltaForceQQbot\.env`；服务器：`/opt/deltaforce-qqbot/.env`。

```dotenv
CHAT_ENABLED=true
CHAT_GROUPS=群号1,群号2
CHAT_ALIASES=机器人别名1,别名2
CHAT_IGNORE_QQ=其它机器人QQ
SUPERUSERS=["你的QQ号"]          # JSON 列表；只有这里的人能清理上下文

LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的key
LLM_MODEL=deepseek-flash
```

常用可调项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHAT_FOLLOWUP_WINDOW` | 180 | 跟进窗口秒数 |
| `CHAT_FOLLOWUP_MAX_REPLIES` | 3 | 窗口内最多再回几次 |
| `CHAT_COOLDOWN` | 20 | 窗口内两次回复的最小间隔 |
| `CHAT_AMBIENT_ENABLED` | false | 没有 @/窗口时是否主动参与 |
| `CHAT_AMBIENT_COOLDOWN` | 30 | 两次主动参与之间的最小间隔 |
| `CHAT_AMBIENT_MIN_CHARS` | 2 | 主动参与时忽略更短的消息 |
| `CHAT_RATE_USER` / `CHAT_RATE_GROUP` | 3 / 20 | 每分钟限流；`DEBUG_GROUPS` 内免限流 |
| `CHAT_MAX_CONTEXT_CHARS` | 6000 | 送进模型的历史字符预算 |
| `CHAT_MAX_INPUT_CHARS` | 2000 | 单条用户消息截断上限 |
| `CHAT_DAILY_TOKEN_BUDGET` | 200000 | 每日 token 上限；超限停止调用 |
| `CHAT_HISTORY_TTL_DAYS` | 7 | 聊天记录保留天数 |
| `CHAT_VISION_ENABLED` | true | 是否把当前图片发给模型 |
| `CHAT_IMAGE_MAX_BYTES` | 8388608 | 单张图最大字节 |
| `CHAT_MAX_IMAGES_PER_MESSAGE` | 4 | 单条消息最多图片数 |

人设写在 `config/persona.md`，文件修改后下一次请求自动重新加载（按 mtime）。
代码里另有一层硬性规则，防止泄露系统提示、编造事实和输出有害内容。

## 4. 存储与隐私

- SQLite：`data/chat.sqlite3`（服务器上归 `deltaforce` 用户，已 gitignore）。
- 表：`messages`（消息/机器人回复）、`summaries`（长期摘要）、`usage`（token 用量）。
- 默认保留 7 天；超期消息在服务启动时清理。摘要会保留到“直接清理”或再次提取。
- 不保存图片字节；只保存 `[图片]` 占位。
- 聊天模块自己的日志只记录群号、用户号、触发类型、token、耗时，不在 INFO 级别打印完整消息；
  NoneBot 自身仍会在连接日志中打印收到的消息（现有行为，未在本功能中改动）。

## 5. 成本与稳定性

- 每次真正调用模型才计费；被忽略的消息只写 SQLite，不调用模型。
- `CHAT_DAILY_TOKEN_BUDGET` 按天汇总；超限后不再调用，并最多每小时在群里提示一次。
- 每群一把 `asyncio.Lock`，避免同一群并发请求串上下文；全局最多 `CHAT_CONCURRENCY` 个请求。
- 模型超时/5xx 重试一次；鉴权失败不重试；失败时直接 @ 的用户会收到一句兜底，跟进窗口内静默。
- 模型 API 走 OpenAI 兼容 `POST {LLM_BASE_URL}/chat/completions`，非流式。

## 6. 部署与验收

没有新增 Python 依赖（`httpx` 已有，SQLite 用标准库），代码更新后：

```bash
export MSYS_NO_PATHCONV=1
.venv/Scripts/python.exe scripts/deploy_server.py --host 39.106.135.148 --user root \
  --skip-assets --skip-deps
```

服务重启后看日志：

```bash
journalctl -u deltaforce-bot -n 100 --no-pager
```

启动日志应出现 `群聊机器人已启用：N 个群，模型 ...`。验收：

1. 测试群 @机器人 说一句话 → 应结合群上下文回复；
2. 不 @ 继续说话（窗口内）→ 按配置继续回，直到次数/冷却限制；
3. 如果开启了 ambient：完全没有 @ 时，等冷却结束后发正常消息 → 应主动接话；
4. 发图片/表情包 → 模型能描述或接梗；
5. 非 `SUPERUSERS` 执行清理 → 被拒绝；
6. `SUPERUSERS` 执行 `提取摘要`/`直接清理` → 生效；
7. 卡战备命令仍然正常，且不会被聊天模型接管。

## 7. 已知限制

- 跟进窗口和 ambient 冷却状态在内存里，服务重启后窗口清空；ambient 仍可在 30 秒后接话，聊天记录本身持久化。
- ambient 默认关闭；需要更主动时设 `CHAT_AMBIENT_ENABLED=true`，再通过 `CHAT_AMBIENT_COOLDOWN` 控制频率。
- 其他机器人默认不会被识别，需要把它们的 QQ 填到 `CHAT_IGNORE_QQ`。
- 图片下载失败、模型不支持图片或图片过大时会降级为文字，不保证每张表情包都能识别。
- 摘要为手动指令，不会自动压缩；超过上下文预算时只截断更早的消息。
- 聊天数据是本群共享的；如果群友对聊天记录留存在意，用 `直接清理` 删除。
