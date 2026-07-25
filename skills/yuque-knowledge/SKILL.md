---
name: yuque-knowledge
description: 通过本地 yuque-mcp 发现、浏览、搜索、读取、备份语雀知识库并查询相邻快照变化。当用户明确提及语雀、知识库列表、知识库目录、语雀文档、知识库备份或语雀变化时使用。
version: 1.0.0
metadata:
  hermes:
    tags: [yuque, knowledge-base, mcp, backup]
---

# 语雀知识库

使用本地 `yuque-mcp` 提供的工具，不自行请求语雀 HTTP API，也不把正文、Token、快照或备份写入普通日志。

## 工具选择

- `list_repos`：列出当前 Token 可读取的知识库元数据。用户只给出“技术交流”“建设方案”“基础学习”等显示名称，或询问有哪些可用知识库时，先调用 `list_repos` 获取 `namespace`。该工具只做发现，不读取正文、不创建快照、不推进变化基线。
- `get_repo_toc`：获取完整、结构化目录。目录浏览和完整枚举优先使用它。
- `list_docs`：快速列出文档元数据；列表接口最多可能只覆盖前 100 篇，不能据此声称知识库完整。
- `search_docs`：按标题搜索，同样受最多 100 篇列表边界影响。
- `get_doc_content`：读取指定 slug 的正文。先通过目录、标题、用户给出的 slug 或 `list_repos` 得到的 namespace 缩小范围，避免批量读取正文。
- `collect_and_get_change_summary`：采集当前完整快照并与上一份完整快照比较。首次调用只有初始化语义；结果是相邻快照的净变化，不是完整版本历史或审计记录。
- `backup_repo`：将知识库导出为 Markdown。仅在用户明确要求“备份”时调用；调用前确认知识库与显示名称，不对同一知识库并发备份，不做无意义重复备份。

## 操作规则

1. 用户询问可访问的知识库列表，或只提供知识库显示名称时，先调用 `list_repos`。
2. 已确认 `repo_namespace` 后，用户要目录或完整枚举时调用 `get_repo_toc`。
3. 用户按标题找文档时调用 `search_docs`，并说明 100 篇边界。
4. 用户指定文档并要求阅读或总结时才调用 `get_doc_content`。
5. 用户询问变化时调用 `collect_and_get_change_summary`，准确说明首次初始化和相邻快照语义。
6. 用户明确要求全库备份时才调用 `backup_repo`；不得把“查看”“总结”理解为备份授权。

## 错误处理

- 认证、权限或上游错误必须如实报告，不得伪装为空结果。
- 工具不可用时说明本地 `yuque-mcp` 连接失败，不切换到远程地址、VPN 或浏览器凭据。
- 不抓取 Cookie、CSRF Token 或浏览器登录状态，不修改语雀文档。
