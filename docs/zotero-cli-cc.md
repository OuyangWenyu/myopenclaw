# Zotero CLI（zotero-cli-cc）

[zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) 是一个面向 AI Agent（Claude Code / Hermes）的 Zotero 命令行工具。本地 SQLite 直读（毫秒响应、离线可用），Web API 安全写入。

## 前置条件

1. **Zotero 桌面版**：需安装并至少运行过一次，确保 `~/Zotero/zotero.sqlite` 数据库文件存在。Zotero 不需要一直运行——读操作直接读 SQLite 文件。
2. **Zotero Web API key**（仅写操作需要）：在 [zotero.org/settings/keys](https://www.zotero.org/settings/keys) 创建 key：
   - 点击 "Create New Private Key"
   - 勾选 **Allow library access** 和 **Allow notes access**
   - 记录生成的 Key 和页面顶部显示的 **User ID**（数字）

## 配置

在 `.env` 中设置：

```bash
# Zotero CLI（zotero-cli-cc）
# 读取无需 API key（SQLite 直读），创建/修改条目需要填写以下两项。
# 在 https://www.zotero.org/settings/keys 创建 key
ZOTERO_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=user
```

- `ZOTERO_LIBRARY_ID` 为你的 Zotero User ID（数字），在 [zotero.org/settings/keys](https://www.zotero.org/settings/keys) 页面可查
- `ZOTERO_LIBRARY_TYPE` 默认 `user`；若使用群组库则填 `group`，`ZOTERO_LIBRARY_ID` 填群组 ID
- 不填则只读模式可用，无法创建/修改条目

容器内 Zotero 数据目录自动从 `~/Zotero`（macOS 默认）挂载到 `/opt/zotero-data`，无需额外配置。

## 常用命令

```bash
# 查看库统计
zot stats

# 搜索文献
zot search "transformer attention"
zot search "deep learning" --limit 10

# 查看条目详情
zot read ABC123

# 通过 DOI 添加条目
zot add --doi "10.1038/s41586-023-06139-9"

# 导出 BibTeX
zot export ABC123 --format bibtex

# 查看最近添加
zot recent --days 7

# 列出合集
zot collection list

# PDF 全文提取
zot pdf ABC123
zot pdf ABC123 --outline          # 仅目录

# 查找/附加缺失 PDF（需要 Zotero 运行 + bridge 插件）
zot find-pdf ABC123
```

## 与 paper-fetch 的联动

完整工作流（Hermes coder 可以自动执行）：

1. **搜索论文** → paper-fetch 找到目标 PDF 并下载
2. **创建 Zotero 条目** → `zot add --doi "..."` 导入元数据
3. **附加 PDF** → `zot attach <key> <pdf_path>` 将 PDF 关联到条目
4. **上传 Google Drive** → `rclone copy <pdf> gdrive:` （已有流程）

示例对话（爱码士 Discord）：
> "帮我找 Attention Is All You Need 这篇论文，下载 PDF 并加到 Zotero"

Hermes 自动执行：paper-fetch 下载 → `zot add --doi` 创建条目 → `zot attach` 附加 PDF → rclone 上传备份。

## 验证

```bash
# 进入容器
docker compose exec hermes-coder zot --version

# 验证数据目录挂载
docker compose exec hermes-coder zot stats

# 搜索测试
docker compose exec hermes-coder zot search "test" --limit 3
```

## 参考

- [GitHub: Agents365-ai/zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc)
- [完整文档](https://agents365-ai.github.io/zotero-cli-cc/)
- [对比其他 Zotero 工具](https://agents365-ai.github.io/zotero-cli-cc/comparison/)
