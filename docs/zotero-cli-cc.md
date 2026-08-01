# Zotero 文献系统

Zotero 文献能力由 [mylibrary](https://github.com/OuyangWenyu/mylibrary) 提供，本项目通过 `zotero-mcp` Docker 服务消费。

## Zotero MCP 服务

| 属性 | 值 |
|------|-----|
| 服务名 | `zotero-mcp` |
| 端口 | 8002 |
| Transport | SSE |
| 端点 | `http://zotero-mcp:8002/mcp` |
| 代码来源 | mylibrary（build-time rsync，本地优先 + git fallback） |

### 12 个 MCP Tools

**Web API**（api.zotero.org，需要 Zotero API key）：

| Tool | 用途 |
|------|------|
| `zotero_search` | 关键词搜索文献库 |
| `zotero_fulltext_search` | 全字段搜索（含 PDF 全文） |
| `zotero_search_by_tag` | 按标签搜索 |
| `zotero_get_item` | 获取单篇文献详情 |
| `zotero_get_recent` | 获取最近添加的文献 |
| `zotero_get_collection_items` | 获取合集下的文献 |
| `zotero_get_collections` | 列出所有合集 |
| `zotero_get_tags` | 列出所有标签 |
| `zotero_get_attachments` | 获取文献附件列表 |
| `zotero_get_annotations` | 获取 PDF 批注（高亮、备注） |

**Local API**（host.docker.internal:23119，需要 Zotero Desktop 运行）：

| Tool | 用途 |
|------|------|
| `zotero_get_fulltext` | 获取索引全文内容 |
| `zotero_get_file_info` | 获取附件文件元数据 |

### 前置条件

1. **Zotero Desktop** 需运行并开启本地 API：Preferences → Advanced → Allow other applications to communicate with Zotero
2. **Zotero Web API key**：在 [zotero.org/settings/keys](https://www.zotero.org/settings/keys) 创建，勾选 Allow library access

### 环境变量

```bash
# .env
ZOTERO_API_KEY=xxxxxxxx          # 爱码士写权限
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=user

# 道元只读（可选，使用独立 API key）
# DAOYUAN_ZOTERO_API_KEY=xxxxxxxx  # 仅 Allow library access
```

## Agent 接入方式

| Agent | 容器 | Zotero 权限 | 接入方式 |
|-------|------|:----------:|----------|
| 爱码士 (coder) | hermes-coder | 读写 | zotero-mcp + paper-to-zotero skill |
| 道元 (daoyuan) | hermes-daoyuan | **只读** | zotero-mcp + zotero-query skill |
| Hermes (default) | hermes | 读写 | zotero-mcp |
| finance | hermes-finance | 只读 | zotero-mcp |

- **爱码士**独享论文注入管线（paper-fetch → rclone → paper-to-zotero），代码来自 mylibrary
- **道元**只能查询文献库，不能创建/修改条目。权限由 Zotero API key 级别控制
- 道元使用 mylibrary 提供的 `zotero-query` skill 进行 MCP 文献查询

## Paper Pipeline（仅爱码士）

完整工作流由 mylibrary 提供，爱码士通过 `run-paper-pipeline.sh` 执行：

1. paper-fetch 搜索并下载 PDF
2. rclone 上传到 Google Drive
3. `paper-to-zotero.py` 创建 Zotero 条目（linked_file 附件）

```bash
# DOI 论文注入
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh '<DOI>'
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh --dry-run '<DOI>'
```

## 验证

```bash
# MCP 连接测试
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test zotero
docker compose exec hermes-daoyuan /opt/hermes/.venv/bin/hermes mcp test zotero

# 健康检查
docker compose exec zotero-mcp python3 -c "print('healthy')"
```

## 参考

- [mylibrary](https://github.com/OuyangWenyu/mylibrary) — Zotero 能力代码源
- [Zotero Web API](https://www.zotero.org/support/dev/web_api/v3/start)
- [pyzotero](https://github.com/urschrei/pyzotero)
