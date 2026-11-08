# 论文清单（paper-queue）

道元（Hermes，飞书）收到「帮我下这篇论文」时**只记清单、不下载**。下载与入库由
`~/code/mylibrary` 离线完成。本页是**契约文档**：库结构、字段语义、读取方式。

> **本清单不追踪消费状态。** 它只回答「谁在什么时候要了哪篇论文」。下载了没有、
> 入库了没有——**靠人判断**，不在库里。

当前状态（2026-11-08 起）：

- **道元是唯一客户端**，通过 Bearer token 固定归属 Owen（单用户阶段）
- 虾酱（OpenClaw）侧的 paper-queue 已下架：不再安装 MCP/插件/skill；
  插件与签名代码保留在仓库，作为未来道元多用户身份插件的参考实现
- 历史 DB 原样保留（同一个 `queue.sqlite`，不迁不删）；旧行的 `requester` 不改写

---

## 1. 库在哪

| 视角 | 路径 |
|---|---|
| 宿主（mylibrary 读这里） | `~/.myagentdata/paper-queue/queue.sqlite` |
| `paper-queue-mcp` 容器内 | `/data/queue.sqlite` |
| **hermes-daoyuan / openclaw-gateway** | **不挂载**队列目录，只能走 MCP 接口 |
| ⚠️ claude-code / hermes / hermes-coder | 通过更宽的 `~/.myagentdata` 挂载仍可达（既有隔离缺口） |

同一个文件，只**直接**挂给 `paper-queue-mcp` 这个独立容器；道元接入不新建库、不复制数据，
历史行原样保留。

> ⚠️ **已知既有缺口（非本次改动引入）**：`claude-code` 以 RW 挂载整个
> `~/.myagentdata`（还 RW 挂载 `~/code`，能读到仓库 `.env`），`hermes` / `hermes-coder`
> 以 RO 挂载 `~/.myagentdata`。因此“队列文件对所有 agent 不可达”目前**不成立**；
> 将 `claude-code` 的 `.myagentdata` 收窄到实际需要的子目录、并把仓库 `.env` 移出
> 其可写范围，是后续独立任务。

> ⚠️ **为什么不挂给 agent**：2026-09-16 实测，虾酱的 `read`/`write` 工具**够得着**
> 那个目录（只有 `exec` 被 allowlist 挡住）。挂在一起时，被提示注入的模型可以绕开
> 一切校验直接改库、甚至改掉服务端代码，签名整套形同虚设。现在它只能走 MCP 接口。

并发约定：两边都必须 `PRAGMA journal_mode = WAL` + `PRAGMA busy_timeout = 5000`。
写入方（`paper-queue-mcp`）是短事务；读取方请只读打开：

```python
import sqlite3
conn = sqlite3.connect("file:~/.myagentdata/paper-queue/queue.sqlite?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM paper_requests WHERE cancelled_at IS NULL ORDER BY requested_at"
).fetchall()
```

**备份**：走 `~/.myagentdata` 的备份链，但 `queue.sqlite` 是**热备**的
（`sqlite3 .backup`，见 `scripts/backup-data.sh`）——WAL 模式下直接拷文件会得到
不一致的副本，所以裸库连同 `-wal`/`-shm` 被排除在 rsync 之外，别指望在快照里
直接 `cp` 它。

---

## 2. 表结构

单表 `paper_requests`，一条记录 = 一次「我要这篇」的请求。完整定义见
`docker/paper-queue-mcp/schema.sql`（它同时是守卫测试的对象，DB 层 CHECK
会拒绝坏数据）。

| 列 | 含义 |
|---|---|
| `id` | 自增主键 |
| `request_key` | 去重键，见 §3 |
| `input_kind` | `title` / `doi` / `arxiv` / `url` —— **用户实际给出的是哪种** |
| `raw_input` | 用户原话片段（人工核对用） |
| `title` | 题目，**来自用户对话**，通常是最可靠的标识 |
| `doi` | DOI，可选 |
| `doi_source` | `user`（用户给的） / `inferred`（模型推断的）—— **见 §4，这条很重要** |
| `arxiv_id` / `url` / `note` | 可选补充 |
| `requester` | **权威**：归属人 ID。道元 token 行固定为 `.env` 的 `PAPER_QUEUE_DAOYUAN_ACTOR_ID`（Owen 的 Discord 雪花）；历史行保留原值 |
| `requester_name` | 显示名，非权威（会变） |
| `channel_ref` / `message_ref` / `session_ref` | 溯源头。道元 token 行：`session_ref="hermes:daoyuan"`，`channel_ref`/`message_ref` 为 NULL |
| `attribution_source` | 归属可信度：`single`（本回合前只收到一条入站）/ `batched`（多条，取最新，可能张冠李戴）/ `NULL`（历史遗留，或道元 token 绑定行 —— token 绑定无法诚实声称前两档，如实置空） |
| `attribution_ambiguous` | 1 = 该请求的归属**可能串台**，见 §6 |
| `requested_at` | 加入时间，**严格 `YYYY-MM-DDTHH:MM:SSZ`**（UTC） |
| `cancelled_at` / `cancelled_by` | 撤销时间与撤销人（成对出现） |

---

## 3. `request_key` 的派生规则

**只从「用户实际说了什么」派生**，优先级：

| 用户给了 | key |
|---|---|
| DOI（且 `doi_source='user'`） | `doi:<小写 DOI>` |
| arXiv ID | `arxiv:<id>`（无版本号） |
| 直链 | `url:<sha256(规范化 URL)>`（去 fragment） |
| 只有题目 | `title:<sha256(小写+折叠空白)>` |

唯一索引**只作用于未撤销的行**（`WHERE cancelled_at IS NULL`）——撤销之后同一篇
可以重新请求。

⚠️ **标题类去重很弱**：同一篇论文措辞一变就是不同的 key，会得到两条记录。契约
**明确允许**这种情况，真正的去重请由消费方（mylibrary，本身幂等）负责。

---

## 4. `doi_source='inferred'` 意味着什么

虾酱**可能会自己推断 DOI**（用户只说了题目时）。这类 DOI：

- 被显式标注为 `inferred`；
- **不参与 `request_key`**（防止一个幻觉 DOI 铸出错误的唯一键、污染去重）；
- **只是线索，不是事实** —— 消费方要用它之前请自行核对（Crossref 等）。

实测（2026-09-16，deepseek-flash）：连续 3 条推断 DOI 经 Crossref 核对**全部正确**。
但这不改变上面三条规则——防的是万一。

---

## 5. 三个工具（道元 / 未来客户端）

MCP server `docker/paper-queue-mcp/server.py`（独立容器，streamable HTTP，端口 8003）：

| 工具 | Hermes（道元）里的名字 | 说明 |
|---|---|---|
| `paper_queue_add` | `mcp__paper_queue__paper_queue_add` | 入队，`items` 是数组（一条消息可入队多篇）。逐条返回 `queued` / `duplicate`（附已存在条目）/ `invalid`（附原因），**部分失败不回滚** |
| `paper_queue_list` | `mcp__paper_queue__paper_queue_list` | 查清单。`window` ∈ `today` / `24h` / `7d` / `all`（默认），或显式 `since`/`until`。**不传 `requester` 时默认查调用者自己**；显式传才查别人 |
| `paper_queue_cancel` | `mcp__paper_queue__paper_queue_cancel` | 撤销。撤销后可重新入队 |

skill 只有一份：`skills/paper-queue/SKILL.md`，OpenClaw 与 Hermes 共用。

时间窗口**一律服务端计算**（"今天"的日界按容器 `TZ`，默认 `Asia/Shanghai`）——
模型不做日期运算。

入参与响应都有体量上限：单次最多 50 篇、单字段最多 2000 字符、`list` 单次响应最多
100k 字符（超限从尾部裁行并标 `truncated: true`）。入参来自模型，没有上限就等于允许
它把几 MB 文本灌进库、再原样塞回上下文。

---

## 6. 归属是怎么保证的（以及它不保证什么）

### 道元（当前）：Bearer token → 固定 actor

- 道元 profile 的 `mcp_servers.paper-queue` 带
  `Authorization: Bearer ${PAPER_QUEUE_DAOYUAN_TOKEN}`
- 服务端把该 token 绑定到固定 actor：
  `PAPER_QUEUE_DAOYUAN_ACTOR_ID=<Owen 的 Discord 雪花>` / `PAPER_QUEUE_DAOYUAN_ACTOR_NAME=Owen`
  （真实 ID 只放 `.env`，不落公开仓库）
- 带该 token 的请求：**忽略**客户端传入的 `actor_*` / `actor_sig`，直接写绑定身份；
  `session_ref="hermes:daoyuan"`，`channel_ref` / `message_ref` / `attribution_source` 为 NULL
- 未知/错误 token 直接拒绝（每个工具都报错、零写入），不会降级到 HMAC
- token 只能写 Owen：即使道元模型读到 token，也无法伪造成别人
- 道元的 Feishu 已收紧为**仅 Owen 私聊**（`FEISHU_GROUP_POLICY=disabled` +
  `FEISHU_ALLOWED_USERS`）；**该变量必须在 `.env` 里填非空**——留空是 Hermes 的
  pairing 模式（会回复配对码），不是 deny-all。填好后当前所有请求确实都来自 Owen

### 旧 OpenClaw HMAC 路径（保留，当前不安装）

> 虾酱不再安装这套链路（start.sh 已移除安装/注入）；以下实现保留在仓库，
> 作为未来道元多用户身份插件的参考。`PAPER_QUEUE_ACTOR_SECRET` 继续保留在 `.env`，
> 但 **compose 默认不注入** `paper-queue-mcp`（注入即重开「持密钥者可写任意 actor」
> 的旁路）。未来需要时取消 compose 里的注释即可恢复。

请求人**不经过模型**：一个 OpenClaw 插件（`openclaw/plugins/paper-queue-actor/`）
在 `before_tool_call` 钩子里把宿主知道的真实 Discord `senderId` 注入工具参数。
模型伪造不了 —— `paper_queue_add` / `paper_queue_cancel` 只认注入的 `actor_id`，
参数里的 `requester` 被忽略；取不到身份时**拒绝写入**，不会用模型填的名字兜底。

**签名（防"插件缺席"）**：插件与服务端共享一个密钥，**唯一来源是 `.env` 的
`PAPER_QUEUE_ACTOR_SECRET`**（`start.sh` 首次运行时生成）—— 历史上 compose 把它注入
sidecar 容器，同一份被写进 `plugins.entries.paper-queue-actor.config`（**当前默认
不注入**，路径休眠）。插件对
`(actor_id, session_ref, actor_message, 请求内容)` 做 HMAC-SHA256，服务端验签后才写入。

> 这一层防的**不是"读"而是"算"**：密钥就写在配置文件里，模型读得到；但它算不出 HMAC。
> 于是"身份注入插件没加载"这种失败会变成**响亮的拒绝写入**，而不是静默采信模型自填的
> 身份 —— 后者伪造出的行，与真实的行在库里**完全无法区分**。
>
> 签名必须覆盖**请求内容**：注入的 `actor_*` 参数会被写进模型可见的会话记录，
> 只签身份的话它只是一枚**可重放的 bearer 值**（模型可以把见过的值配上任意 `items` 重放）。
>
> **密钥缺失时拒绝写入**，不静默跳过 —— 否则一次"env 被删"就会让防护无声消失。
> 读操作（`paper_queue_list`）不验签：`requester` 本就是可显式传的公开参数。

### 信任边界

保护分两层：

1. **容器隔离（2026-09-16 起）**：服务跑在独立容器 `paper-queue-mcp` 里，队列目录
   **只挂给那个容器**。虾酱所在的 openclaw-gateway 里根本没有这个路径 —— 实测
   `ls /home/node/.myagentdata` 返回 "No such file or directory"。它只能走 MCP 接口。
2. **工具参数通道**：即使够得着接口，模型也无法通过调用参数伪造归属（见上）。
   写入工具连 `requester` 参数都没有，伪造无处可传。

**仍不在保护范围内的**：

- **宿主用户**（以及能写宿主文件的一切）—— 那等于"能登你机器的人"，无法也不该由本
  设计防。
- `paper-queue-mcp` 容器自身若被攻破 —— 但它不跑模型、不接受任意代码，攻击面只有
  这 7 个 MCP 工具（其中 4 个是 `mcp` 库自带的 prompts/resources 空壳，碰不到队列数据）。

**非用户触发的回合不产生记录。** 只有**用户消息触发**的回合才会绑定身份。
cron / heartbeat / 命令行注入的回合没有入站消息，插件**不绑定**、工具调用随之拿不到
身份、服务端拒绝写入。早先的版本会退回"该会话最近发言者" —— 那等于瞎猜，会把请求
记到错误的人头上；现在宁可不写。

⚠️ **已知局限——归属是「回合」粒度，不是「消息」粒度**：同一回合内**后到**的消息会
继承先到者的绑定。若两条消息来自**不同的人**，后者会被算到前者头上。

- `attribution_source` 会如实标注这次绑定有多可信（见 §2）；
- 插件检测到同回合内出现**不同发言人**时会打 `attribution_ambiguous = 1`；
- 复核办法：用 `message_ref` 回 Discord 搜那条原始消息。

---

## 7. 自检工具

```bash
python3 scripts/check_paper_queue.py            # 人类可读
python3 scripts/check_paper_queue.py --json     # JSON（cron/监控用）
```

它**不经过模型、不依赖虾酱在线**，直连只读打开数据库，检查：schema 列完整性、
逐行不变量（无标识 / 非规范时间戳 / 撤销字段不成对 / 非法枚举）、活跃键重复、
以及各时间窗口的计数与 `attribution_ambiguous` 计数。

退出码：`0` 健康（或尚未初始化） / `1` 发现不一致 / `2` 脚本自身错误。
注意「库存在但表不在这台机器上」会被判为 `1` 而不是"空清单"——避免指错库时静默报绿。

**「库被删了」能被查出来**：server 首次建库时会在库旁边留一个
`queue.sqlite.initialized` 哨兵。检查器看到**哨兵在、库没了**会报 `queue_deleted`
并退 1 —— 否则"被删"和"从没用过"表现完全一样，删库不会触发任何告警。
（哨兵与库都在备份覆盖范围内；从快照恢复后两者一致。）

---

## 8. 装机与卸载（道元）

装机走 `./scripts/start.sh`（幂等）：

- 建 `~/.myagentdata/paper-queue/`（历史库原样沿用）
- 缺失时生成 `PAPER_QUEUE_DAOYUAN_TOKEN` 写入 `.env`（不打印 token）
- 调 `scripts/ensure_hermes_paper_queue.py` 把 `mcp_servers.paper-queue`
  写入道元 profile（占位符形态，真实值由容器 env 展开）
- 把 `skills/paper-queue` 通过 compose 只读挂载 + symlink 接进道元 profile
- 清理虾酱侧残留：`remove_openclaw_paper_queue.py` 删除 `mcp.servers.paper-queue` /
  `plugins.entries.paper-queue-actor` / `plugins.allow` 条目，并删除
  `~/.openclaw/skills/paper-queue` 与 `~/.openclaw/extensions/paper-queue-actor`

轮换 token：删掉 `.env` 里的 `PAPER_QUEUE_DAOYUAN_TOKEN=` 行，重跑 `./scripts/start.sh`；
`up -d` 会重建 `paper-queue-mcp` 与 `hermes-daoyuan`（`restart` 不会更新容器 env）。

关闭道元接入：**持久开关是清空 `.env` 的 `PAPER_QUEUE_MCP_URL=`（并一并清掉
`PAPER_QUEUE_DAOYUAN_TOKEN`，否则 token 仍在容器里可被直接调用）**，重跑 `start.sh`。
`ensure_hermes_paper_queue.py --disable` 只对当次生效，下一次 `start.sh` 会重新注册。

服务端代码改动（`server.py`）需要重建镜像：
`docker compose build paper-queue-mcp && ./scripts/start.sh`。

虾酱侧不需要再做任何操作；若旧机器仍有残留，`./scripts/start.sh` 会自动清理。

> ⚠️ 重启网关前请确认它**没有正在跑启动迁移**——迁移期间重启会把它打断、下次从头再来，
> 表现是网关"看起来卡死"（不绑端口、不报错）。静置几分钟会自行恢复。详见 `CLAUDE.md`。
