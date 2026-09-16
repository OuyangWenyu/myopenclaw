# 论文清单 — 虾酱只记不下，mylibrary 离线消费

> **本 PRD 于 2026-09-16 收缩范围。** 原版要求虾酱追踪"是否已下载/已入库"（三态机 +
> `%PDF` 魔数证据 + 对账器），用户明确将其移出 MVP：**"其实就是只实现一个清单的效果…
> 虾酱先别记录是否被消费了，这一点就不在 MVP 里实现。我们先把清单记录做好，然后能返回，
> 以及比如按时间范围返回 —— 我今天加入清单的、或者过去 24 小时加入清单的 list 能给出来，
> 我觉得就行了，靠人来判断。"** 被移出的条目见 §Scope 的 Out of scope 表。

## Problem

虾酱（Discord 主机器人）被要求下载论文时是**即发即下**：一次只能处理一篇，短时间内攒了
几篇就得逐条说明；而且下完就散落在工作区，**没有任何台账**。请求过什么、哪篇成功了、
哪篇还没做——全部不可知。

更糟的是失败**完全静默**：实测虾酱工作区里 28 个 `.pdf` 中有 8 个根本不是 PDF，而是
Cloudflare 挑战页 / 出版商过渡页被原样存成了 `.pdf`。用户看到文件存在，会以为下载成功了。

**代价**：批量场景下逐条输入的低效与遗漏（issue #64 自述）；以及无法区分「已入库」与
「看起来像已入库」。

## Evidence

**实测（2026-09-15，命令 `head -c 5 | grep %PDF`）**

- `~/.openclaw/workspace/{papers,pdfs}` 共 **28 个 `.pdf`**，**20 真 / 8 假**（29%）。
- 假样本原文：`Crow_2026_WRR_Streamflow_Calibration.pdf` → `<!DOCTYPE html>...<title>Just a moment...</title>`（Cloudflare 挑战页）；`Li_2018_PNAS_water_ice.pdf` → "Preparing to download" 过渡页。
- ⚠️ **诚实修正**：这 28 个的时间跨度是 2026-05-06 ~ **2026-08-01**，**最近 6 周没有新增**。
  因此不能说"正在持续发生"——更可能是「因为不好用所以停用了虾酱下载」。这个歧义本身就是
  待验证项（见 Open Questions）。

**架构实测（决定了本需求的形状）**

- 虾酱容器 `/home/node/.openclaw/skills/` 里**没有** `paper-to-zotero`（那是爱码士的 skill）。
- 虾酱 `openclaw.json` 中零处 zotero / rclone / gdrive 引用；容器内无 rclone。
- ⇒ **虾酱结构上不可能完成"同步 Zotero"**，职责收缩为**只记不下**（用户已确认）。

**输入形状实测（2026-09-16，真实使用）**

- 用户**通常直接说题目**，DOI **不一定有**；虾酱有时会自己推断 DOI（实测 3 条推断 DOI
  经 Crossref 核对全部正确，但仍按"线索"而非"事实"处理）。

**基线缺失**：当前没有任何「请求数 / 成功率 / 完成时延」记录。建立这个基线本身是本需求的一部分。

## Users

- **Primary**: Owen 本人 —— 在 Discord 群里 @虾酱，一次可能连着丢多篇。
- **Secondary（请求者角色）**: 群内其他成员 —— 可以提出请求，是队列记录的 `requester` 来源；
  他们能查自己的清单，但**不是消费方**。
- **Not for**: 其他 agent（本轮不接入）；mylibrary 是**消费方**而非用户（它的需求由契约满足）。

## Hypothesis

We believe **把"下载论文"改成"先记账"——虾酱只写清单、不碰下载** will **让 Owen 一次性丢进
多篇而不必逐条盯，且随时能按时间范围问出"我要过哪些"** for **在 Discord 群里使用虾酱的场景**。

We'll know we're right when：单条消息可入队 ≥5 篇；问「今天加入的清单」能准确返回；
清单里的题目/DOI 与用户所说一致；工作区不再新增任何 PDF。

**明确不承诺**（本 MVP 范围外）：清单不记录下载是否发生、是否入库；这些**靠人判断**。

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| 批量入队 | 单条消息可入队 **≥5 篇** | 实测：一次丢 5 篇，查清单条数 |
| 时间范围查询 | 「今天」/「过去 24 小时」返回**准确条数** | `paper_queue_list(window=...)` 与库中 `requested_at` 对照 |
| 归属可信 | `requester` = **真实 Discord 用户 ID**，且非 `session_fallback` | 查库比对 `attribution_source`；实测已验证 |
| 重复请求 | `duplicate` **不产生第二条活跃记录** | 同一篇连说两次，活跃条数不变 |
| 孤儿 PDF 增长 | **0 个/周**（基线：28 个，含 8 个假 PDF） | `find ~/.openclaw/workspace -name '*.pdf'` 计数 |
| 清单内容准确 | 题目与用户所述一致；`inferred` 的 DOI 有标注 | 人工抽样核对 |

## Scope

**MVP** — 本仓库（myopenclaw）交付：

1. **清单契约**：`openclaw/skills/paper-queue/schema.sql`（单表 + DB CHECK），作为单一事实来源供 mylibrary 读取。
2. **虾酱写入路径**：收到下载指令 → 入队，**不下载**。
3. **虾酱查询路径**：按**时间范围**（今天 / 24h / 7d / 全部）与按人返回清单。
4. **撤销**：记错了可撤销，撤销后可重新入队。
5. **归属注入**：宿主级身份注入（不经模型），保证多人不串台——**可检出**的串台会被打标。
6. **契约文档**（`docs/paper-queue.md`）+ **守卫测试**。

**Out of scope**

| 项 | 归属 |
|---|---|
| **下载执行本身** | mylibrary（用户已定） |
| **Zotero 入库 / GDrive 上传** | mylibrary |
| **⚠️ 消费状态追踪**（`downloaded`/`consumed` 三态机、`%PDF` 魔数证据、失败原因枚举、对账器） | **2026-09-16 移出 MVP**：靠人判断，不进库 |
| **题目 → DOI 的解析** | 消费方（mylibrary）。清单只存用户给的内容，并标注 `inferred` 来源 |
| 爱码士侧入口 | 本轮只做虾酱单入口 |
| 队列消费的调度（手动/launchd/定时） | mylibrary 侧决定 |
| 自动重试策略 | 不做；重新说一次即产生新记录 |
| Web UI / 看板 | 无需求 |
| 清理 workspace 里已有的 28 个孤儿 PDF | 一次性数据整理，另办 |

## Delivery Milestones

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | 清单契约 | schema + `request_key` 规则定稿；mylibrary 可据此开工 | complete | `.claude/plans/paper-download-queue.plan.md` |
| 2 | 虾酱写入 + 查询 | 群里丢一篇/多篇 → 立即可查、可按时间范围查；不产生新的工作区 PDF | complete | 同上 |
| 3 | mylibrary 读取（**另仓库**） | 读清单 → 下载（幂等，跳过已下的）→ 入库 | pending | mylibrary issue |

> M1/M2 的详细执行计划见 `.claude/plans/paper-download-queue.plan.md`；契约文档见 `docs/paper-queue.md`。

## Open Questions

- [x] **队列 DB 落地路径** —— **结论**：`~/.myagentdata/paper-queue/queue.sqlite`（跨仓库共享数据的既有归处，与虾酱生命周期解耦），openclaw-gateway 加**窄挂载**（只映射该子目录），`backup-data.sh` 补 SQLite 热备。
- [x] **去重策略** —— **结论**：`UNIQUE(request_key) WHERE cancelled_at IS NULL`（活跃期唯一，撤销后可再请求）。`request_key` **只从用户实际说的内容派生**；`inferred` 的 DOI 不参与。
- [x] **失败重试入口** —— **结论**：不做自动重试；重新说一次即产生新记录；撤销走 `cancelled_at`。
- [x] **多篇切分** —— **结论**：由模型切分成 `items[]`（一条消息多篇 → 一次调用）。切错可用 `paper_queue_cancel` 撤销。
- [x] **实现手段** —— **结论（实测）**：走 **MCP stdio server**（工具调用不经 exec approvals；白名单路径不可行——`askFallback=deny` 且官方劝退解释器白名单）。`command-dispatch: tool` 零模型直派**不可用**（MCP 工具不在直派工具面内）⇒ 斜杠命令走"路由给模型"。
- [x] **群成员的请求** —— **结论**：入队，归属由宿主插件注入（**不可由模型伪造**），取不到身份即拒绝写入。每人可查自己的清单；Owen 不承担人工核对别人清单的职责。
- [ ] **已完结记录的保留期**：行是否长期保留？（当前：永久保留，撤销只是打标）
- [ ] **6 周无新增孤儿 PDF 的原因**：是"停用虾酱下载"还是"改用别的方式"？若是前者，本需求的实际使用率假设需要复核。

### 本轮设计暴露并已处理的问题

- [x] **归属是 run 粒度不是消息粒度** —— 同一回合内后到的消息继承先到者的绑定，多人接连发言会串台。**处理**：插件检出并打 `attribution_ambiguous=1`，检查器单独计数，**不静默**；`message_ref` 可回溯原始消息。
- [x] **零模型直派拿不到 MCP 工具** —— **处理**：放弃直派，斜杠命令走模型路由（skill 选择仍确定）。

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **清单成死信箱**：虾酱写、没人消费 | 中 | 中 | 清单价值在"看得见"；mylibrary 读取是 M3；检查器给出各时间窗口计数 |
| **归属串台**（同回合多人发言） | 中 | 中 | 打标 + 计数 + `message_ref` 可溯源；不静默 |
| **推断 DOI 被当成事实使用** | 中 | 中 | `doi_source='inferred'` 显式标注；不参与唯一键；`docs/paper-queue.md` 明写"仅供消费方参考" |
| **标题弱去重**（同篇不同措辞 → 两条） | 高 | 低 | 契约明说允许；真正去重交消费方（mylibrary 幂等） |
| 虾酱误把非下载意图入队 | 中 | 低 | SKILL.md 明确触发语与"不要用"的场景；提供撤销 |
| 清单库不在备份范围内 | 中 | 中 | 已纳入 `backup-data.sh` 的 SQLite 热备（缺 sqlite3 失败退出） |
| **重启网关打断启动迁移**（运维陷阱，实测踩过） | 中 | 高 | `start.sh` 先比对容器 `StartedAt`，重建过就不重启；已写进 `CLAUDE.md` |

---
*Status: MVP 已实现并真机验证（2026-09-16）。M3 在 `~/code/mylibrary`。*
