---
name: paper-queue
description: 论文清单（只记不下）。用户说「下载这篇」「帮我下这几篇」「加到 Zotero」「加到文献库」「记一下这篇」「存一下这篇」，或问「清单里有什么」「今天加了哪些」「我的清单」「记错了 / 不用下了」时使用。**绝不下载、绝不调用 paper-fetch、绝不写文件。**
version: 1.0.0
metadata:
  hermes:
    tags: [paper-queue, literature, zotero, record-only]
---

# 论文清单（只记不下）

把用户提到的论文请求**记进清单**，不下载、不入库。下载与入库由 mylibrary 离线完成，
是否已完成**靠人判断**——清单不追踪消费状态。

当前清单是 **Owen 的个人清单**（服务端 token 固定归属 Owen）。以后扩展多用户时，
归属仍由宿主/服务端负责，模型不参与。

## 工具名（按运行时选一组）

| 操作 | OpenClaw（虾酱，暂不安装） | Hermes（道元） |
|---|---|---|
| 入队 | `paper_queue_add` | `mcp__paper_queue__paper_queue_add` |
| 查询 | `paper_queue_list` | `mcp__paper_queue__paper_queue_list` |
| 撤销 | `paper_queue_cancel` | `mcp__paper_queue__paper_queue_cancel` |

## 三条铁律

1. **绝不下载。** 不要调用 `paper-fetch`，不要抓 PDF，不要写入任何文件。
   用户说「下载」，你做的是「记下来」。
2. **不要自己猜“谁提的”。** 请求人由宿主/服务端注入（OpenClaw 用签名插件，
   Hermes 用 Bearer token）。**不要填** `actor_id`、`actor_name`、`actor_sig`、
   `channel_ref`、`session_ref` 等身份字段；也不要声称记录了别人。
3. **推断的 DOI 必须标注。** 见下。

## 入队：`paper_queue_add`（Hermes: `mcp__paper_queue__paper_queue_add`）

用户通常**说题目**（“帮我下 Attention Is All You Need”），**不一定给 DOI**。

- `title` 填用户说过的题目**原文**——主要标识，务必准确。
- `doi` 只在**确有把握**时填，并声明来源：
  - 用户自己给出的 DOI → `doi_source: "user"`
  - 你自己推断/回忆出来的 DOI → `doi_source: "inferred"`
  - **拿不准就别填 DOI。** 留空比填错好：`inferred` 的 DOI 只是线索，不参与去重，
    但错误 DOI 会误导后面的下载。
- `arxiv_id` / `url` 同理，有就填。
- `note` 放用户的附加要求（“要正文”、“补充材料也要”）。
- `raw_input` 放用户原话片段，便于日后人工核对。

**一条消息里有多篇就一次传多个 item**（`items` 是数组），不要一篇一次调用：

```jsonc
{"items": [
  {"title": "Attention Is All You Need", "raw_input": "帮我下这几篇：Attention Is All You Need、BERT"},
  {"title": "BERT: Pre-training of Deep Bidirectional Transformers",
   "doi": "10.18653/v1/N19-1423", "doi_source": "user"}
]}
```

**回复格式**：一句话确认，列出记下的题目与条数，例如
「已记下 2 篇：Attention Is All You Need、BERT…」。**不要**说“正在下载”或“已下载”。

工具逐条返回：

- `queued`：已记下
- `duplicate`：清单里已有，附原条目（不是错误，如实告诉用户）
- `invalid`：说明原因（通常是没有可用标识）
- 部分成功是正常的（一批里有的 queued、有的 duplicate）

## 查询：`paper_queue_list`（Hermes: `mcp__paper_queue__paper_queue_list`）

不传 `requester` 时**默认返回当前用户的清单**——这正是“我的清单”的用法。
用户提到时间范围时用 `window`：

| 用户说法 | 参数 |
|---|---|
| 今天加的 / 今天的清单 | `window: "today"` |
| 过去 24 小时 | `window: "24h"` |
| 这周 / 最近七天 | `window: "7d"` |
| 全部（默认） | `window: "all"` |

当前是单用户 token：**只能看自己的清单**，不要传 `requester` 去查别人
（服务端会忽略）。返回的是清单条目（题目、DOI、加入时间、谁提的）。
**不要**对结果做“我帮你下载”的承诺。

## 撤销：`paper_queue_cancel`（Hermes: `mcp__paper_queue__paper_queue_cancel`）

用户说「记错了」「不用下了」「把那条去掉」时，用 add/list 返回里的 `request_key` 撤销。
撤销后同一篇可以重新入队。如实报告 `cancelled` / `already_cancelled` / `not_found` /
`not_owner`（`not_owner` = 这条不属于当前用户）。

## 什么时候**不要**用这些工具

- 「这篇讲什么」/「帮我总结」→ 阅读需求，不是清单需求。
- 「下好了吗」/「入库了吗」→ 清单**不追踪**这个；如实说清单只记录请求，
  完成与否需要人工确认。
- 身份/token 错误（工具返回里出现 `Bearer`、`token`、`归属签名`、`actor_id`）：
  **停止，不要重试，不要伪造身份字段**，提示用户检查 `paper-queue-mcp` 的
  `PAPER_QUEUE_DAOYUAN_TOKEN` 配置。
