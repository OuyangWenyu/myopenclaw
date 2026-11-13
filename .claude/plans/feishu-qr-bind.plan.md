# Plan: 飞书扫码创建一个 Hermes agent，并只给它一份默认能力

**来源**: 2026-11-13。家庭 agent 不能挂进现有四个 profile 的共享目录；要让扫码和这边的 agent 真的连上，而不是只生成一个二维码。
**复杂度**: High（新的本机绑定服务 + 运行时容器，不改现有四个 Hermes 的清单）
**分支**: 继续 `refactor/hermes-agent-manifest`
**先写计划，不在本文件落地代码**

## 摘要

飞书官方 SDK `@larksuiteoapi/node-sdk` 的 `registerApp`（OAuth 2.0 Device Authorization Grant，RFC 8628）会给出一个十分钟、一次性的验证链接。用户用飞书扫这个链接，新建一个企业自建应用。SDK 返回 `client_id`、`client_secret`，以及扫码用户的 `user_info.open_id`。

绑定服务拿到这三项之后，才创建**一个新的 Hermes 容器**。这个容器使用独立数据目录，飞书环境变量就是这三项目，记忆提供者是 `local`。扫码人是唯一允许私聊的人。容器日志里出现飞书长连接，并且这个人的私聊能得到回复，才算连上。

现有的爱玛士、爱码士、finance、道元不动。他们的应用也不会出现在扫码页的「选择已有应用」里。

## 已核对的接口

来源是飞书开放平台「一键创建智能体应用」和 `@larksuiteoapi/node-sdk` 1.61.1 及以上的 `registerApp`（对照过 1.72.0 的说明）。

| 事实 | 含义 |
|---|---|
| `onQRCodeReady({ url, expireIn })` 给出验证链接 | 页面把这个 URL 画成二维码。链接约 10 分钟、一次一人 |
| 成功返回 `client_id`、`client_secret` | 这就是 Hermes 要的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` |
| 成功还可返回 `user_info.open_id`、`user_info.tenant_brand` | `open_id` 写成这个 agent 的白名单。字段在类型上是可选的 |
| `createOnly: true` | 确认页只有「新建应用」，不能选已有应用。避免扫码时改写爱玛士或斐斐现有应用的权限 |
| `addons.preset: false` | 丢掉平台默认的「智能体清单」。那份清单含文档写入、云盘、建群、通讯录。默认机器人不要这些 |
| `addons.scopes` / `addons.events` | 在最小基座（只有机器人能力）上补我们列出的权限和事件 |
| 长连接是抢消息，不是广播 | 同一个 App ID 只能有一个网关。第二个进程会抢同一条连接 |
| 空的 `FEISHU_ALLOWED_USERS` 是配对模式，不是拒绝所有人 | 没有 `open_id` 时不能启动网关 |
| Hermes 入口脚本看到非空且不是 `memory_tencentdb` 的 `provider` 会保留 | 启动前把 `provider: local` 写进配置，家庭对话不会进个人长期记忆 |
| 入口脚本在 `GITHUB_TOKEN` 非空时会写 gh 的 `hosts.yml` | 这个容器的环境里不能有 GitHub、语雀、飞书 CLI、邮箱这些变量 |
| 飞书长连接是容器主动连出去的 | 不需要给这个容器发布宿主端口，也不需要公网回调 |

## 什么叫「连上」

下面五条都成立，才算扫码和 agent 连上。少一条都不算。

1. 扫码返回的 `client_id` 出现在这个容器的 `FEISHU_APP_ID` 里，且全机没有第二个容器使用同一个值。
2. 扫码返回的 `open_id` 是 `FEISHU_ALLOWED_USERS` 的唯一值。
3. 容器使用自己的数据目录，配置里是 `memory.provider: local`。
4. 网关日志出现飞书长连接已建立。
5. 扫码人发一条私聊，得到这条 agent 的回复。换一个飞书账号发，得不到回复。

第 5 条要真人扫一次。自动化测试用一份假的返回值把容器拉起来，检查前四条里可以离线验证的部分。

## 默认给这个 Hermes 什么

两层分开写。飞书应用的权限决定机器人在飞书里能碰什么。容器的环境决定模型能碰什么。两边都用白名单，不从现有四个 agent 的公共环境变量复制。

### 飞书应用（`addons.preset: false` 之后显式补上）

| 项 | 值 |
|---|---|
| 能力 | 机器人 |
| 应用身份权限 | `im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly`、`im:message:send_as_bot`、`im:resource` |
| 事件 | `im.message.receive_v1` |
| 用户身份权限 | 不申请。不做「代替用户发消息、读用户文档」 |
| 回调 | 不订 `card.action.trigger`。默认没有审批按钮 |

`im:resource` 是为了收图片。markdown 卡片只负责把回复画出来，不依赖按钮回调。文档、日历、通讯录、建群、云盘都不在这份默认里。以后某个人要文档能力，是给他单独加权限，不是改默认。

### 容器

镜像用现有的 `myopenclaw/hermes:latest`。模型是 `deepseek-flash`，兜底 `zai/glm-5.3-flash`，`agent.reasoning_overrides` 里 `glm-5.3-flash: low`。这两把钥匙从仓库根 `.env` 读入，是这个容器仅有的模型钥匙。

资源先按 2G 内存、1 个 CPU。它不跑论文流水线，也不用现有 agent 的 4G / 2 CPU。

| 给 | 不给 |
|---|---|
| 独立目录 `~/.hermes-agents/<agent_id>/` 挂到 `/opt/data` | `~/.hermes`、`~/.myagentdata`、OneDrive、Zotero |
| `memory.provider: local`，`memory_enabled: true` | `MEMORY_TENCENTDB_*`，不进共享长期记忆 |
| 镜像里自带的技能 | 不挂 `~/.hermes/skills`，不挂仓库里的晨报、语雀、研发日报、事务库技能 |
| 飞书长连接、白名单、群聊必须 @ | 邮箱、GitHub、GitCode、飞书 CLI、语雀 key、Docker socket |
| `HERMES_KANBAN_DISPATCH_IN_GATEWAY=0` | 不加入看板派发，也不能被派成工人 |
| 加入 `myopenclaw-net`，以便访问外网模型 | 不注册 server-monitoring、dailyinfo、paper-queue、aisecretary、zotero-mcp |
| 卡片和公式开关与现有 Hermes 相同（`FEISHU_MARKDOWN_CARD` 等） | 不发布宿主端口 |

群策略是 `allowlist`。群里只有白名单上的人 @ 它才回答。默认没有群 id，所以一开始只有扫码人的私聊。

事务不接入现有的 `transactions.sqlite`。这个人的对话和本地记忆只在他自己的目录里。

## 组件

```
本机浏览器（只监听 127.0.0.1）
    → agent-bind 服务
        → registerApp 轮询飞书
        → 成功后写 ~/.hermes-agents/<id>/{config.yaml,.env,bind.json}
        → docker compose -f ~/.hermes-agents/compose.yml up 这个服务
            → 容器 hermes-bind-<id>
                → 飞书长连接
```

`agent-bind` 是唯一持有 Docker socket 的新进程。它启动的 Hermes 容器没有 socket。绑定页面不暴露到局域网以外：谁都能打开页面，谁就能让我们的 DeepSeek 钥匙挂到他新建的飞书应用上。扫码的人只扫二维码，不调用这个接口。

`~/.hermes-agents/compose.yml` 和每个 agent 的 `.env` 留在宿主，不进 git。仓库里只提交默认能力文件和绑定服务的代码。

`bind.json` 记录 `agent_id`、`client_id`、`open_id`、`tenant_brand`、容器名、创建时间。不记录 `client_secret`。secret 只在该目录的 `.env`（权限 600）。

## 一次绑定的顺序

1. 操作者在本机打开绑定页，创建一个会话。服务调用 `registerApp`，`createOnly: true`，`addons.preset: false`，权限和事件用上一节的表。`appPreset.name` 用 `"{user}的助手"`，扫码人仍可在飞书页改名。
2. `onQRCodeReady` 把 URL 交给页面。页面画二维码，并显示剩余时间。会话取消或超时则 `AbortSignal` 中止轮询。
3. 用户扫码并确认新建。`registerApp` 返回凭据。
4. `user_info.open_id` 为空：删除这次会话的临时文件，不创建容器，页上写明失败原因。不使用配对模式兜底。
5. `open_id` 有值：生成 `agent_id`，写配置和 `.env`，再创建容器。写文件用临时名，写完再改成正式名。容器创建失败则保留目录但把状态写成 `failed`，不留下一个用了半套凭据的进程。
6. 启动后读日志，直到看到长连接成功，或超过 90 秒。90 秒内失败则停掉这个容器，状态写成 `failed`。
7. 页上显示「已连上」和机器人名称。验收时由扫码人发一条私聊。

同一个 `client_id` 再次出现：不创建第二个容器，页面指向已经在跑的那个。

## 容器里的配置形状

`~/.hermes-agents/<agent_id>/profiles/owner/config.yaml`：

```yaml
model:
  default: deepseek-flash
  provider: deepseek
  base_url: https://api.deepseek.com
fallback_providers:
  - zai
fallback_model:
  provider: zai
  model: glm-5.3-flash
agent:
  reasoning_overrides:
    glm-5.3-flash: low
memory:
  memory_enabled: true
  provider: local
```

环境变量只有默认能力表里的键。`HERMES_PROFILE=owner`，这样入口脚本里那些「只在默认 profile 才做」的邮箱初始化不会跑。`FEISHU_ALLOWED_USERS` 就是扫码返回的那一个 `open_id`。

挂载只有两样：这个目录到 `/opt/data`，以及挡住邮箱配置和 token 的空卷。不挂 `data`（那是 `~/.hermes`）。

## 和现有清单的关系

`agents/hermes/order` 仍是 default、coder、finance、daoyuan。扫码创建的 agent 不写进这份清单，也不写进仓库里的 `docker-compose.yml`。那份文件一改，现有四个容器的配置哈希就要重新对。

默认能力写成仓库里的 `agents/hermes/bind-defaults.yaml`，绑定服务只读它。测试钉住：渲染出来的环境变量键集合与这份文件一致，且不含 `GH_TOKEN`、`MCP_YUQUE_MCP_API_KEY`、`LARK_CLI_APP_SECRET`、`MEMORY_TENCENTDB_GATEWAY_HOST`。

## 备份

现有 `hermes/scripts/backup.sh` 只打包 `~/.hermes`。绑定成功的目录必须进备份，否则云盘上没有这些人的对话。不放进 `backup-data.sh`：它的根是 `~/.myagentdata`。做法是扩展 `hermes/scripts/backup.sh`，有 `~/.hermes-agents` 时另存到 `$BACKUP_ROOT/hermes-agents/`，排除每个 `.env` 和入口脚本写出的 `secrets/`。`backup-cron` 增加这个目录的只读挂载。凭据不进云盘快照；换机时重新扫码绑定。对话和 `config.yaml` 要进快照。个人 `~/.hermes` 不存在时也不能跳过这一份。

## Tasks

### 1. 默认能力文件

写 `agents/hermes/bind-defaults.yaml`：飞书权限、事件、容器环境变量键、资源限制、挂载。测试只解析这份文件，不启动 Docker。

### 2. 用假凭据把容器拉起来

绑定服务接受一份测试返回值（`client_id`、`client_secret`、`open_id`），走真正的写目录和 `docker compose up`。不调用飞书。

验收：

- 容器在跑，环境变量键与默认能力文件一致
- `config.yaml` 里是 `provider: local`
- 数据目录不是 `~/.hermes`
- 没有第二个容器使用同一个 `client_id`
- `open_id` 为空时进程不创建

做完停掉这个测试容器，删掉它的目录。

### 3. 接上 `registerApp`

绑定页只听 `127.0.0.1`。SDK 版本在绑定服务的依赖里钉死。日志和页面都不打印 `client_secret` 和完整 `.env`。

真人扫一次码（斐斐或另一个测试飞书账号，新建应用，不选已有应用）。然后：

- 容器日志有飞书长连接
- 扫码人私聊得到回复
- 另一个飞书账号私聊得不到回复

这一步过了，才叫扫码和 agent 连上。

### 4. 备份

`~/.hermes-agents` 进入数据备份，`.env` 排除。用一份不含 secret 的假目录跑备份脚本的路径断言。

## 不在这版里

- 不把绑定页公开到公网，不做注册登录
- 不改现有四个 Hermes 的清单、飞书应用和记忆
- 不把扫码 agent 接进看板、事务库、团队 MCP
- 不给默认机器人文档、日历、通讯录权限
- 不在扫码时选择或改写已有应用
- 不为每个 agent 再配一个网页仪表盘

## 回滚

停掉 `agent-bind`，再停掉 `hermes-bind-*` 容器。`~/.hermes-agents` 留在磁盘上，不自动删除。现有四个容器没有改过配置，不受影响。
