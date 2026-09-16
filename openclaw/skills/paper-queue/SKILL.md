---
name: paper_queue
description: 论文清单。用户说「下载这篇」「帮我下这几篇」「加到文献库」「同步到 Zotero」「存一下这篇」，或问「清单里有什么」「今天加了哪些」「我的清单」时使用。**只记不下**：不下载、不调用 paper-fetch、不碰 Zotero。
user-invocable: true
---

# 论文清单（只记不下）

把这个机器人收到的论文请求**记进清单**，不下载、不入库。下载与入库由别处离线完成，
是否已完成**靠人判断**——清单不追踪消费状态。

## 三条铁律

1. **绝不下载。** 不要调用 `paper-fetch`，不要抓 PDF，不要在 workspace 里留任何文件。
   用户说「下载」，你做的是「记下来」。
2. **不要自己去猜"谁提的"。** 请求人由宿主注入到工具参数里（`actor_id`），
   工具会用它。你不需要、也不应该填 `requester`。
3. **推断的 DOI 必须标注。** 见下。

## 入队：`paper_queue_add`

用户通常会**说题目**（"帮我下 Attention Is All You Need"），**不一定给 DOI**。
所以：

- `title` 填用户说过的题目**原文**——这是主要标识，务必准确。
- `doi` 只在**确有把握**时填；而且必须声明来源：
  - 用户自己给出的 DOI → `doi_source: "user"`
  - 你自己推断/回忆出来的 DOI → `doi_source: "inferred"`
  - **拿不准就别填 DOI。** 留空比填错好：`inferred` 的 DOI 只是线索，不参与去重，
    但错误的 DOI 会误导后面的下载。
- `arxiv_id` / `url` 同理，有就填。
- `note` 放用户的附加要求（"要正文"、"补充材料也要"）。
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
「已记下 2 篇：Attention Is All You Need、BERT…」。**不要**说"正在下载"或"已下载"。

工具会逐条返回 `queued`（已记）/ `duplicate`（清单里已有，附原条目）/ `invalid`（说明原因）。
`duplicate` 不是错误，如实告诉用户"这篇已经在清单里了"即可。

## 查询：`paper_queue_list`

不传 `requester` 时**默认返回提问者自己的清单**——这正是"我的清单"的用法。
用户提到时间范围时用 `window`：

| 用户说法 | 参数 |
|---|---|
| 今天加的 / 今天的清单 | `window: "today"` |
| 过去 24 小时 | `window: "24h"` |
| 这周 / 最近七天 | `window: "7d"` |
| 全部（默认） | `window: "all"` |

要查**别人**的清单，显式传 `requester`（Discord 用户 ID）。

返回的是清单条目（题目、DOI、加入时间、谁提的）。**不要**对结果做"我帮你下载"的承诺。

## 撤销：`paper_queue_cancel`

用户说「记错了」「不用下了」「把那条去掉」时，用返回里的 `request_key` 撤销。
撤销后同一篇可以重新入队。

## 什么时候**不要**用这些工具

- 「这篇讲什么」/「帮我总结」→ 这是阅读需求，不是清单需求。
- 「下好了吗」/「入库了吗」→ 清单**不追踪**这个，如实说清单只记录请求，
  完成与否需要人工确认。
