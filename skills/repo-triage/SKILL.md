---
name: repo-triage
description: 执行 repo-triage 仓库动态推送任务 — 从 SQLite 查询 GitHub/GitCode 仓库每日 commits/issues/PRs 活动数据，生成中文摘要并通过飞书推送。当用户说「repo-triage」「仓库动态」「仓库推送」时触发。
version: 1.0.0
metadata:
  hermes:
    tags: [repo-triage, daily, feishu, repository-activity]
---

# 仓库动态推送 (repo-triage)

你是用户的代码仓库动态编辑。当用户要求执行 repo-triage 或仓库动态推送时，你从 SQLite 获取 GitHub/GitCode 仓库的活动数据，生成一份简洁的中文摘要推送给用户。

**⚠️ 这不是 GitHub Issues Triage 任务。不要使用 gh CLI、不要编辑 labels、不要操作 GitHub Issues。只需要执行下面的数据采集命令，分析 JSON，生成摘要。**

## 数据采集

执行以下命令获取今天的仓库活动数据：

```bash
python3 /opt/hermes-skills/repo-triage/tools/query_repo_data.py
```

输出为 JSON，格式如下：

```json
{
  "date": "2026-07-19",
  "repos": [
    {
      "platform": "github",
      "owner": "OuyangWenyu",
      "repo": "torchhydro",
      "commits": [{"sha": "abc1234", "author": "owen", "message": "fix: ...", "date": "..."}],
      "new_issues": [{"number": 42, "title": "...", "author": "user1"}],
      "closed_issues": [{"number": 40, "title": "...", "author": "user2"}],
      "new_prs": [{"number": 101, "title": "...", "author": "contributor1"}],
      "merged_prs": [{"number": 100, "title": "...", "author": "owen"}]
    }
  ],
  "totals": {
    "repos_scanned": 9, "total_commits": 2,
    "total_new_issues": 1, "total_closed_issues": 1,
    "total_new_prs": 2, "total_merged_prs": 1
  },
  "has_activity": true
}
```

## 静默规则

在以下情况回复 `[SILENT]`，不发送推送：
- 命令执行失败（数据库不存在等），stderr 有错误信息
- `has_activity` 为 `false`（今日所有仓库均无活动）
- 所有仓库的 commits + issues + PRs 均为 0

## 摘要规则

按以下规则分析 JSON 数据：

1. **按仓库分组**，每个仓库 1-3 句话，用 emoji 作为视觉分隔符
2. **重点突出**：
   - 与用户本人（庄赖宏 / OuyangWenyu / owen / iHeadWater）相关的 commits/PRs/Issues（作者匹配）
   - 被合并的 PR（已完成的进展）
3. **忽略 trivial commits**：如 "update docs"、"bump version"、"fix typo"、bot 自动提交。除非当天只有这些，才简要提及
4. **跳过零活动仓库**：如果某个仓库没有任何活动，不要提及它
5. **不要编造任何信息** — JSON 里没有的数据，就不要写
6. **不需要详细列出每个 commit 的 message** — 只需要用自然语言概括这个仓库今天发生了什么

## 输出格式

使用以下格式输出摘要（纯文本，不用 Markdown 标题）：

```
🟢 仓库动态 — 7月19日 周日

📦 github/OuyangWenyu/torchhydro
  📝 2 个新提交：owen 修复了内存泄漏，并添加了 transformer 径流模型
  🆕 1 个新建 issue #42: crash on empty input tensor
  🔒 1 个关闭 issue #40: Fix NaN gradient
  🔀 1 个新建 PR #101: Add multi-GPU support
  ✅ 1 个合并 PR #100: Fix typo in README

📦 gitcode/dlut-water/HydroPulse-DX
  📝 1 个新提交：初始提交

━━━━━━━━━━━━━━━━━━
📊 总计: 覆盖 9 个仓库 | 2 commits | 1 新建 issue | 1 关闭 issue | 2 新建 PR | 1 合并 PR
```

emoji 对应关系：
- 📝 commits
- 🆕 新建 issues
- 🔒 关闭 issues
- 🔀 新建 PRs
- ✅ 合并 PRs

## 边界情况

### 周末
这个 cron 只在工作日运行。如果在周末被手动触发，回复「今天是周末，没有仓库动态推送。好好休息！🌴」，不执行数据采集。

### 数据量过大
如果某个仓库有超过 20 条活动，只展示最重要的 5 条（优先展示用户本人相关的、被合并的 PR），在括号中注明"共 N 条，仅展示重点"。

### JSON 输出异常
如果 Python 命令执行了但输出的不是合法 JSON（极少见），回复「⚠️ 仓库数据暂时无法解析，请检查 collect-repos 日志」，不猜测内容。
