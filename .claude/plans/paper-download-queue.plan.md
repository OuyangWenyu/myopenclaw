# 论文清单（paper-queue）— 交付记录

> **这份文件已收缩为交付记录。** 范围在 2026-09-16 由用户明确定为"**只做一个清单**"：
> 不追踪消费状态、不做 `%PDF` 证据、不做入库对账。需求见
> `.claude/prds/paper-download-queue.prd.md`，契约见 `docs/paper-queue.md`。
> 执行级计划与逐条 spike 记录见会话产物；本页只留"做了什么、为什么这么设计"。

## 交付内容

| 产物 | 位置 |
|---|---|
| 契约（单表 + DB CHECK，schema v3） | `docker/paper-queue-mcp/schema.sql` |
| MCP server（独立容器，FastMCP streamable HTTP，3 个工具） | `docker/paper-queue-mcp/server.py` |
| 给模型的指令 | `openclaw/skills/paper-queue/SKILL.md` |
| 身份注入插件（3 钩子 + HMAC 签名） | `openclaw/plugins/paper-queue-actor/` |
| 服务镜像 | `docker/paper-queue-mcp/Dockerfile`（新容器 `paper-queue-mcp`，端口 8003） |
| 只读检查器（不经模型） | `scripts/check_paper_queue.py` |
| 幂等配置注入 | `scripts/ensure_openclaw_paper_queue.py` |
| 契约文档（给 mylibrary） | `docs/paper-queue.md` |
| 守卫测试（5 个文件，181 项） | `tests/test_paper_queue_*.py`、`tests/test_ensure_openclaw_paper_queue.py` |

装机走 `./scripts/start.sh`（幂等）；库在 `~/.myagentdata/paper-queue/queue.sqlite`。

## 关键设计决定（含代价）

1. **只记不下**：`paper-fetch` 被配置禁用（`skills.entries`），"只记不下"由机器强制而非提示词。
   *代价*：失去即发即下能力，需要时临时置回 `true`。
2. **身份由宿主注入，不经模型**（`message_received` 采集 → `before_tool_call` 注入）。
   *代价*：多一个插件要维护；插件挂了写入会被拒（fail closed，不会静默伪造）。
3. **归属是 run 粒度**：同回合内后到的消息继承先到者的绑定。**如实标注**
   `attribution_source ∈ {single, batched}`（更早的历史值在 schema v3 迁移时置 NULL）
   + `attribution_ambiguous`，
   不静默。*代价*：多人同回合发言仍可能串台，只是**看得出来**。
4. **去重键只从用户实际说的内容派生**：`inferred` 的 DOI 不参与。
   *代价*：标题类去重很弱（措辞不同即两条），真正的去重交消费方。
5. **库在 `~/.myagentdata/paper-queue/`，且只挂给独立容器 `paper-queue-mcp`**。
   *代价*：多一个常驻容器（256M/0.5CPU）+ 备份脚本要加热备。
   *为什么*：2026-09-16 实测虾酱的 `read`/`write` 工具够得着那个目录（`exec` 被挡，
   文件工具没挡），挂在一起时被提示注入的模型能绕开签名直接改库。
6. **签名覆盖请求内容**：防"插件缺席时模型重放见过的签名"。
   *代价*：两边签名材料必须逐字一致，不一致的表现是"每次写入都被拒"。
   密钥唯一来源是 `.env` 的 `PAPER_QUEUE_ACTOR_SECRET`（start.sh 生成）。

## 已知并接受的限制

见 `docs/paper-queue.md` 的「信任边界」一节。**容器隔离 + 工具参数校验**两层已覆盖
"模型伪造归属"；仍不在范围内的是**宿主用户**（= 能登机器的人，不可防也不该防）。

## 验证（2026-09-16 真机）

真实 Discord 消息验证通过，关键证据：落库行的 `requester` 是真实 Discord 雪花、
`attribution_source=single`、`attribution_ambiguous=0`、`request_key` 是 `arxiv:` 而非标题哈希。
这一条数据同时证明：密钥流转、HMAC 双向一致、签名覆盖内容、库迁移后新枚举可写。

**容器隔离实测（同日）**：让虾酱当场试过 —— `exec` 被 allowlist 挡住，但 `read`/`write`
**够得着** `~/.myagentdata/paper-queue/`（会话记录里有 `read` 该路径的 toolResult、以及
一条写进去又回读成功的 `probe.txt`）。据此把服务搬进独立容器；搬完后网关里
`ls /home/node/.myagentdata` 返回 No such file or directory。

守卫：`uv run --with pytest --with pyyaml pytest tests/ -q`（全量 367 项）+ `for t in tests/test-*.sh`。
