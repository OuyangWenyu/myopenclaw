# 千问办公 AI听记（QwenNote）MCP 接入（Hermes · 仅默认 profile）

> 最后更新：2026-10-09

默认 profile（爱玛士）经远程 MCP 接入千问办公 AI听记（QwenNote，钉钉录音卡 A1 的升级产品；服务端 `minutes.qwennote.cn` 为阿里官方云服务），可查询与整理本人的听记录音转写、AI 摘要、待办、标签与热词。

**授权边界（重要）**：只有**默认 profile（爱玛士）**接入。爱码士 / 道元 / finance 各自拥有独立的 profile 配置（`~/.hermes/profiles/<name>/config.yaml`）与独立的 token 目录，均不注册本服务——个人听记不向其他 agent 开放。

## 能力（16 个工具）

读取（11 个）：

| 工具 | 用途 |
|------|------|
| `list_my_minutes` | 列本人创建的听记（时间/关键词筛选、分页） |
| `list_minutes_tags` / `list_minutes_by_tag` | 标签列表 / 按标签浏览 |
| `get_minutes_basic_info` / `batch_get_minutes_details` | 单条 / 批量基础信息 |
| `get_minutes_ai_summary` | AI 摘要（Markdown） |
| `get_minutes_keywords` | 关键词 |
| `get_minutes_transcription` | 逐段转写原文（发言人 + 时间戳） |
| `list_minutes_todos` | AI 提取的待办（含钉钉待办同步标记） |
| `get_minutes_media_url` | 音频限时播放直链 |
| `list_hot_words` | 个人热词 |

写入（5 个）：`update_minutes_title`、`update_minutes_summary`（覆盖摘要）、`replace_minutes_text`（全文纠错替换）、`replace_speaker`（发言人归属）、`add_personal_hot_word`。

## 架构与认证

- 端点：`https://minutes.qwennote.cn/mcp`（streamable HTTP）。标准 MCP OAuth 2.1：动态客户端注册（RFC 7591）+ PKCE（S256）+ 公共客户端（`token_endpoint_auth_method=none`）。scope 仅 `qwennote:read` / `qwennote:write`；授权服务器支持 `refresh_token` 自动续期与 `/revoke` 撤销。
- token 落盘在 `~/.hermes/mcp-tokens/qwennote.{json,client.json,meta.json}`（mode 600）—— **不进 git、不进镜像**；也不在 backup-cron 的备份清单里，换机后重跑一次授权即可（这是有意的：refresh token 属于账号凭据，不进云备份）。
- 注册与授权分两步（脚本在仓库 `scripts/`）：
  1. `bootstrap_hermes_qwennote.py` — 幂等注册 `mcp_servers.qwennote` 到默认 profile 配置（外科文本编辑 + 自动备份，不要求宿主 PyYAML）；
  2. `qwennote_oauth_login.py` — 容器内的授权助手：打印授权链接 → 浏览器登录授权 → 浏览器跳转 `http://127.0.0.1:<port>/callback` 并报「无法连接」（**预期行为**：回调监听在容器内，宿主浏览器够不到）→ 把地址栏完整 URL 粘贴回终端完成兑换。
- **为什么不用 `hermes mcp login`**：无头容器内该命令的探测会并发启动两条授权流、抢同一个回调端口，第二条必然 `EADDRINUSE` 并带崩整个进程（2026-10-09 实测，Hermes 2026.9.x 镜像）。助手脚本只跑单条流、纯粘贴换码、不监听任何端口。

## 一次性接入

```bash
# 1. 注册（幂等；--dry-run 预览；--disable 移除）
./scripts/bootstrap_hermes_qwennote.py

# 2. 授权（交互式：按提示在浏览器完成，然后粘贴回调 URL）
docker compose cp scripts/qwennote_oauth_login.py hermes:/tmp/
docker compose exec hermes /opt/hermes/.venv/bin/python3 /tmp/qwennote_oauth_login.py

# 3. 验证
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test qwennote    # → ✓ Connected / 16 tools

# 4. 生效（网关按会话加载 MCP）
docker compose restart hermes

# 5. 隔离自检：三个受限 profile 不应出现 qwennote
docker compose exec hermes-coder   /opt/hermes/.venv/bin/hermes -p coder   mcp list
docker compose exec hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan mcp list   # 应只有 zotero
docker compose exec hermes-finance /opt/hermes/.venv/bin/hermes -p finance mcp list
```

功能验收：飞书私聊爱玛士「列一下我的听记标签」。

## Profile 隔离机制（为什么只有爱玛士）

- Hermes 四个 profile 是四个**隔离实例**：默认 profile 的 home 就是 `~/.hermes`（配置 = 根 `config.yaml`）；coder / daoyuan / finance 的 home 是 `~/.hermes/profiles/<name>/`，各自拥有独立 `config.yaml` 与运行时状态。
- 本服务的注册只写默认 profile 配置；受限 profile 既无配置也无 token（token 目录同样按 profile home 隔离）。
- ⚠️ 检查某个 profile 的**真实视图**必须带 profile 上下文：`hermes -p <profile> mcp list`。裸 `hermes mcp list` 在任意容器里读到的都是**默认 profile** 的配置，会产生「四个容器共享注册」的错觉。

## 排障

| 现象 | 说明 / 处理 |
|------|------|
| 日志 `parking until credentials change … no cached tokens` | 该进程没有可用 token。首次授权完成前出现属正常；授权后仍出现则重跑授权助手。受限 profile 的容器**不应**出现（若出现说明有旧进程残留，`docker compose restart <容器>`） |
| `mcp test` 返回 401 / invalid_token | refresh token 被撤销或过期：重跑 `qwennote_oauth_login.py` |
| 日志 `suspicious description content — concealment instruction`（工具 `list_minutes_todos`） | **已知误报**：该工具描述里 "…do not tell the user that a to-do's status is unknown…" 命中了 Hermes 的藏匿指令正则（`do not (tell\|inform\|mention\|reveal)`）。原意是防止 agent 把字段缺失误报成失败；扫描器仅告警不拦截（源码注释明言 false positive 不应破坏正常服务），无需处理 |
| 撤销授权 | 服务端 `/revoke` 端点；本地 `rm ~/.hermes/mcp-tokens/qwennote.*` 并 `./scripts/bootstrap_hermes_qwennote.py --disable` |

## 安全边界

- 凭据（client_id / access / refresh token）只存在于本机 `~/.hermes/mcp-tokens/`；仓库仅含公开端点与占位符。
- scope 含 `qwennote:write`：5 个写工具可改标题/摘要/发言人并做全文替换。听记转写是「他人可影响的输入」，存在提示注入面——不需要写能力时，可在 `mcp_servers.qwennote` 下加 `tools: {exclude: [update_minutes_title, update_minutes_summary, replace_minutes_text, replace_speaker, add_personal_hot_word]}` 收敛（Hermes 支持 include/exclude 工具过滤）。
- 读取的听记内容会随对话进入 Hermes 主模型（DeepSeek API），与既有数据流一致。
