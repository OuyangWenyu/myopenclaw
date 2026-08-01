# Plan: Zotero MCP 双通道 — Web API + Local API 全文获取

**Source PRD**: Issue #29 (revised per deep-research findings)
**Complexity**: Medium

## Summary

当前 `zotero-mcp-server.py` 仅支持 Web API，无法获取 linked_file 附件的 PDF 全文。
本次改造增加 Local API（`host.docker.internal:23119`）作为第二通道：
Web API 负责搜索/元数据（不受桌面端影响），Local API 负责全文获取（利用宿主机 Zotero Desktop）。
所有函数补全类型注解（ECC Python 规则硬性要求）。

## Background (调研结论)

用户的 Zotero PDF 存储在 Google Drive，通过 `linked_file` 附件链接。Web API 不存储/不代理这类文件，因此必须走 Local API（Zotero Desktop 的 `localhost:23119` HTTP 服务）获取全文。
宿主机 24/7 运行 Zotero Desktop，`host.docker.internal:host-gateway` 已在 docker-compose 中配置（L58-59, L249-250）。

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Naming | `zotero-mcp-server.py:143` | Tools: `tool_zotero_<name>(args) -> str` |
| Naming | `zotero-mcp-server.py:215` | Tool schemas: `TOOLS` list of `{"name", "description", "inputSchema"}` |
| Errors | `zotero-mcp-server.py:72-92` | API errors: `{"error": True, "message": "..."}` dict |
| Errors | `zotero-mcp-server.py:372-380` | Tool exceptions: catch and wrap in `{"error": True, ...}` |
| HTTP client | `zotero-mcp-server.py:59-92` | `urllib.request` stdlib, no external deps |
| MCP dispatch | `zotero-mcp-server.py:325-383` | `_handle_request()` dispatching `initialize`/`tools/list`/`tools/call` |
| MCP registration | `entrypoint.sh:289-295` | `settings.mcpServers["zotero"]` with command/args/env |
| Volume mount | `docker-compose.yml:199-200` | `:ro` suffix for read-only mounts |
| Env vars | `docker-compose.yml:214-217` | `${VAR_NAME:-default}` pattern with comments |
| Docker COPY | `Dockerfile:66` | `COPY <file> /opt/<file>` for scripts |

## Files to Change

| File | Action | Why |
|---|---|---|
| `docker/claude-code/zotero-mcp-server.py` | UPDATE | 加 Local API 客户端 + 2 个新 tool + 类型注解 |
| `docker/claude-code/entrypoint.sh` | UPDATE | MCP server 配置传入 `ZOTERO_LOCAL_API_URL` env var |
| `docker-compose.yml` | UPDATE | claude-code 服务增加 `ZOTERO_LOCAL_API_URL` env var |
| `CLAUDE.md` | UPDATE | 更新 Zotero MCP 文档说明双通道架构 |

## Tasks

### Task 1: 重构 API 层 — 双后端抽象

- **Action**: 
  1. 添加 `LOCAL_API_BASE` 常量（默认 `http://host.docker.internal:23119`）
  2. 抽取 `_web_api_get(path)` — 现有 `_api_get` 改名，Web API 专用
  3. 新增 `_local_api_get(path)` — 对 Local API 发 GET 请求，无需 API key，timeout 5s（本地应更快）
  4. 保持现有 `_api_get` 作为 Web API 别名（向后兼容）
- **Mirror**: 现有 `_api_get` 的错误处理模式（HTTPError/URLError/OSError/JSONDecodeError）
- **Validate**: 无语法错误：`python3 -c "import ast; ast.parse(open('docker/claude-code/zotero-mcp-server.py').read())"`

### Task 2: 新增 `zotero_get_fulltext` tool（Local API）

- **Action**: 
  1. 添加 `tool_zotero_get_fulltext(args)` 函数
  2. 调用 `GET http://host.docker.internal:23119/<itemKey>/fulltext`
  3. 入参：`item_key` (required)
  4. 返回：JSON `{content, indexedPages, totalPages}` 或 error
  5. 添加 `TOOLS` schema 条目
  6. 注册到 `TOOL_MAP`
- **Mirror**: `tool_zotero_get_item` 的参数校验模式（检查必需参数）
- **Validate**: `python3 -m py_compile docker/claude-code/zotero-mcp-server.py`

### Task 3: 新增 `zotero_get_file` tool（Local API）

- **Action**: 
  1. 添加 `tool_zotero_get_file(args)` 函数
  2. 调用 `GET http://host.docker.internal:23119/<itemKey>/file`
  3. 入参：`item_key` (required)
  4. 行为：返回文件基本信息（filename, contentType, size），而不是原始二进制（避免 MCP 传输大 PDF）
  5. 对 linked_file 附件：返回 path 字段，让 CC飞总 能定位文件
  6. 添加 `TOOLS` schema 条目
  7. 注册到 `TOOL_MAP`
- **Mirror**: `tool_zotero_get_item` 的单 item 查询模式
- **Validate**: MCP tools/list 输出包含全部 6 个 tool

### Task 4: 补全类型注解

- **Action**: 
  1. 所有函数签名添加类型注解（`def _rate_limit() -> None:`）
  2. 导入 `from typing import Any`（需要时）
  3. 涉及函数：`_rate_limit`, `_api_url`, `_web_api_get`, `_local_api_get`, `_format_item`, `_parse_int`, 4 个现有 tool 函数，2 个新 tool 函数，`_write_response`, `_write_error`, `_handle_request`, `main`
- **Mirror**: ECC Python rules — "Use type annotations on all function signatures"
- **Validate**: `python3 -c "import ast; ..."` 语法检查通过

### Task 5: 更新 MCP 配置注入 env var

- **Action**: 
  1. 在 `entrypoint.sh` 的 `settings.mcpServers["zotero"]` 块中添加 `env` 对象
  2. 传入 `ZOTERO_LOCAL_API_URL: "http://host.docker.internal:23119"`
  3. 保留现有 `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`, `ZOTERO_LIBRARY_TYPE`（由 docker-compose env 传入）
- **Mirror**: tdai-memory 的 `env` 注入模式（entrypoint.sh L282-285）
- **Validate**: JavaScript 语法在容器启动时不会报错

### Task 6: 更新 docker-compose.yml

- **Action**: 
  1. 在 `claude-code` 服务添加 `ZOTERO_LOCAL_API_URL=http://host.docker.internal:23119`
  2. 在注释中说明 Local API 的用途
- **Mirror**: 现有 ZOTERO_* 环境变量模式（L243-245）
- **Validate**: `docker compose config | grep ZOTERO`

### Task 7: 更新 CLAUDE.md 文档

- **Action**: 
  1. 更新 Zotero MCP 注释块，说明双通道架构
  2. 注明 Local API 需要宿主机 Zotero Desktop 运行
- **Mirror**: 现有 CLAUDE.md 中 Zotero MCP 段落的注释风格
- **Validate**: Markdown 语法正确

## Validation

```bash
# 语法检查
python3 -m py_compile docker/claude-code/zotero-mcp-server.py

# MCP 协议握手测试（在容器内）
docker compose exec claude-code bash -c 'echo '\''{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'\'' | python3 /opt/zotero-mcp-server.py'

# tools/list 验证（6 tools）
docker compose exec claude-code bash -c 'echo '\''{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'\'' | python3 /opt/zotero-mcp-server.py | python3 -c "import json,sys; tools=json.loads(json.loads(sys.stdin.read())['result']['tools']); print(f'{len(tools)} tools:', [t['name'] for t in tools])"'

# 确认 env var 注入
docker compose exec claude-code bash -c 'grep ZOTERO_LOCAL_API_URL /home/node/.claude/settings.json'
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Local API 不可达（Zotero Desktop 未运行/未开启 API） | Medium | 启动时检测 Local API 连通性，不可用时只禁用 fulltext/file tool，搜索/元数据仍可用 |
| `host.docker.internal` DNS 解析失败 | Low | 已在 docker-compose 配置 `extra_hosts`，长期稳定运行 |
| Local API 响应格式与 Web API 不一致 | Low | 两个 API 使用同一数据模型，测试验证 |
| 文件体积过大导致 MCP 响应超时 | Low | `zotero_get_file` 只返回文件信息（不返回二进制），由 CC飞总 按需读取 |

## Acceptance

- [ ] 6 个 MCP tools 全部可用（4 原有 + 2 新增）
- [ ] 所有函数有类型注解
- [ ] Web API tools 在 Local API 不可用时仍正常工作
- [ ] Local API tools 在 Local API 不可达时返回清晰错误信息
- [ ] CLAUDE.md 文档准确反映新架构
