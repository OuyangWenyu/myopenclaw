---
name: yuque-knowledge
description: 通过已配置的 yuque-mcp 发现、浏览、搜索、读取、备份语雀知识库并查询服务端已生成的文档变化报告。当用户明确提及语雀、知识库列表、知识库目录、语雀文档、知识库备份或语雀变化时使用。
version: 1.0.0
metadata:
  hermes:
    tags: [yuque, knowledge-base, mcp, backup]
---

# 语雀知识库

使用已配置的 `yuque-mcp` 提供的工具。不要自行请求语雀 HTTP API，不要抓取 Cookie、CSRF Token 或浏览器登录状态，不要修改语雀文档，不要把正文、Token、MCP_API_KEY、认证头、快照、数据库或备份内容写入普通日志。

## 工具选择

- `list_repos`：列出当前 Token 可读取的知识库元数据。用户只给出“技术交流”“建设方案”“基础学习”等显示名称，或询问有哪些可用知识库时，先调用 `list_repos` 获取 `namespace`。该工具只做发现，不读取正文、不创建快照、不推进变化基线。
- `get_repo_toc`：获取完整、结构化目录。目录浏览、层级理解和完整枚举优先使用它。
- `list_docs`：快速列出文档元数据；列表接口可能存在分页或数量边界，不能仅凭它声称知识库完整。
- `search_docs`：按标题搜索。若未命中，不得断言文档一定不存在；必要时改用 `get_repo_toc` 核对完整目录。
- `get_doc_content`：读取指定 slug 的正文。先通过目录、标题、用户给出的 slug 或 `list_repos` 得到的 namespace 缩小范围，避免批量读取正文。
- `get_change_summary`：只读取服务端最新生成的指定知识库变化报告。该工具不会创建快照、不会读取语雀正文、不会推进基线；结果是服务端固定快照之间的净变化，不是自然日日报、完整版本历史或官方审计记录。
- `backup_repo`：将知识库导出为 Markdown。仅在用户明确要求“备份”时调用；调用前确认知识库与显示名称，不对同一知识库并发备份，不做无意义重复备份。

## 操作规则

1. 用户询问可访问的知识库列表，或只提供知识库显示名称时，先调用 `list_repos`。
2. 如果 `list_repos` 返回多个同名或近似名称知识库，不要自行选择；列出候选的 `name`、`namespace` 和 `description`，请用户确认。
3. 已确认 `repo_namespace` 后，用户要目录、层级或完整枚举时调用 `get_repo_toc`。
4. 用户按标题找文档时调用 `search_docs`；若未命中，不得断言文档一定不存在，必要时改用 `get_repo_toc` 核对完整目录。
5. 用户指定文档并要求阅读、解释或总结时才调用 `get_doc_content`。读取正文后只摘取完成任务所需内容，不无请求地大段复述全文。
6. 用户询问单个知识库变化时调用 `get_change_summary`。它只读取服务端已生成报告；如果返回 `not_available`，说明服务端尚未完成调度生成。
7. 用户询问多个知识库变化时，先用 `list_repos` 确认每个知识库的 `namespace`，再分别调用 `get_change_summary`；不要把多个知识库合并成一个 MCP 调用。
8. `get_change_summary` 返回 `initialized` 时，说明服务端首次快照已建立基线，不是失败；需要下一次服务端 07:00 报告生成后才能看到变化。
9. 变化报告必须说明它是服务端固定快照之间的净变化，不是自然日日报、完整版本历史或官方审计日志。
10. 用户明确要求全库备份时才调用 `backup_repo`；不得把“查看”“总结”理解为备份授权。

## 错误处理

- 认证、权限或上游错误必须如实报告，不得伪装为空结果。
- 工具不可用时说明 `yuque-mcp` 连接失败，不切换到其他地址、VPN、浏览器凭据或非公开接口。
- 缺少 `MCP_API_KEY`、服务返回 401 或连接失败时，只说明 MCP 认证或连接失败；不要要求用户提供 `YUQUE_TOKEN`。
- 文档或知识库不存在时，优先用 `list_repos`、`get_repo_toc` 或 `search_docs` 帮用户缩小范围，不凭名称猜测 `namespace` 或 slug。
