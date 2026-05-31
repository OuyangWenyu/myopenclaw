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

## 自动安装与配置链

`./scripts/start.sh` 启动时自动完成以下步骤，无需手动操作：

```
.env (ZOTERO_API_KEY, ZOTERO_LIBRARY_ID, GDRIVE_PAPERS_LOCAL_PATH)
  │
  ├─→ docker-compose.yml  将变量注入容器
  │     ├─→ entrypoint-wrapper.sh  读取 env vars → 生成 /opt/data/.config/zot/config.toml
  │     └─→ GDRIVE_PAPERS_LOCAL_PATH  传入容器（paper-to-zotero.py 使用）
  │
  └─→ start.sh  调用 install_paper_to_zotero_skill()
        ├─→ ~/.hermes/skills/paper-to-zotero/        （全局 skill，所有 profile 可用）
        └─→ ~/.hermes/profiles/coder/skills/research/paper-to-zotero/  （爱码士专属）
              └─→ 自动 git init + commit（Hermes 只识别有 .git 的 skill 目录）
```

- **两个安装路径**：全局 `~/.hermes/skills/` 和 coder profile `~/.hermes/profiles/coder/skills/research/`。爱码士优先读 coder profile 路径
- **`.git` 目录是必须的**：Hermes 只扫描带 `.git` 的目录作为 skill。`start.sh` 会自动 `git init` + `commit`
- **Zotero 凭证**：在 `.env` 中配置后，`entrypoint-wrapper.sh` 自动生成 `config.toml`，`paper-to-zotero.py` 和 `zot` CLI 都从那里读取，Agent 无需向用户索要 key
- **GDRIVE_PAPERS_LOCAL_PATH**：必须在 `.env` 中正确设置。注意 Google Drive 路径因机器而异（macOS 用户名不同、CloudStorage 路径不同等）。默认值从 `.cloud.conf` 推导不准确，建议手动确认
- **`zotero-upload` 劫持风险**：如果爱码士在对话中自建了 Zotero 相关 skill（如 `zotero-upload`），会劫持所有 Zotero 请求。检查 `~/.hermes/profiles/coder/skills/research/` 下是否有非预期的 skill 目录

## 与 paper-fetch 的联动

完整工作流（Hermes coder 通过 paper-to-zotero skill 自动执行）：

1. **搜索并下载论文** → paper-fetch 找到目标 PDF 并下载到 `/tmp/papers/`，导出 JSON 到文件
2. **上传 Google Drive** → `rclone copy <pdf> gdrive:` 上传 PDF 到 Google Drive（主存储）
3. **创建 Zotero 条目 + linked_file 附件** → `paper-to-zotero.py <json>` 一次性创建完整元数据条目和 linked_file 附件（路径自动从 `$GDRIVE_PAPERS_LOCAL_PATH` + JSON 中的文件名拼接）

**元数据来源**：Crossref API（期刊论文，完整作者列表、摘要、期刊/卷/页码）→ arXiv API（预印本兜底）→ paper-fetch meta（最后兜底）。

**附件类型**：`linked_file` 指向本地 Google Drive 路径（如 `~/Google Drive/我的云端硬盘/Documents/Papers/Zotero_Papers/file.pdf`）。Google Drive macOS 客户端以 stream 模式管理文件，在 Finder 里显示为占位符，点击时自动下载。不占用 Zotero 免费存储空间（300MB）。

**注意**：PDF 以 Google Drive 为主存储（免费、不限量），Zotero 只存元数据索引。

示例对话（爱码士 Discord）：
> "帮我找 Attention Is All You Need 这篇论文，下载 PDF 并加到 Zotero"

Hermes 自动执行：paper-fetch 下载 → rclone 上传 → `paper-to-zotero.py` 创建完整 Zotero 条目（含元数据和 linked_file 附件）。

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
