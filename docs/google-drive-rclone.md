# Google Drive 集成（rclone）

Hermes 通过 rclone 直接上传论文 PDF 到 Google Drive 的目标文件夹。不走 volume mount，用 Google Drive API + OAuth 认证，权限限定到单个文件夹。

## 为什么不用 volume mount

Google Drive 桌面客户端在 macOS 上使用 File Provider API，「流式文件」模式下文件是云端占位符（placeholder），Docker 容器写入的文件会触发 `Operation not permitted`，宿主机和 Zotero Desktop 都无法读取。详见下方说明。

## 安全模型

- OAuth token 存储在 `~/.hermes/rclone/rclone.conf`（`chmod 600`，不进 git）
- rclone remote 的 `root_folder_id` 限定到目标文件夹，无权访问 Google Drive 其他内容
- Hermes 容器只有 rclone 命令行工具，没有 Google Drive 的读写权限以外的任何能力

## 首次配置（新机器）

### 1. Google Cloud Console

1. 打开 [Google Cloud Console](https://console.cloud.google.com) → 选择或创建项目
2. 左上角汉堡菜单 → **APIs & Services** → **Enable APIs and Services** → 搜索并启用 `Google Drive API`
3. **Credentials** → **+ Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app**
   - 名称随便填（如 `rclone-hermes`）
4. 创建后下载 JSON（`client_secret_*.json`）

### 2. 宿主机安装 rclone 并授权

```bash
brew install rclone

# 提取 client_id 和 client_secret
CLIENT_ID=$(python3 -c "import json; d=json.load(open('client_secret_*.json')); print(d['installed']['client_id'])")
CLIENT_SECRET=$(python3 -c "import json; d=json.load(open('client_secret_*.json')); print(d['installed']['client_secret'])")

# 运行授权（会弹出浏览器，登录 Google 账号）
rclone authorize drive "$CLIENT_ID" "$CLIENT_SECRET"
```

完成后会输出一段 token JSON，包含 `access_token` 和 `refresh_token`。

### 3. 写入配置文件

将以下内容写入 `~/.hermes/rclone/rclone.conf`（`chmod 600`）：

```ini
[gdrive]
type = drive
client_id = <从 client_secret JSON 提取>
client_secret = <从 client_secret JSON 提取>
token = {"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}
root_folder_id = <目标文件夹 ID>
```

**获取 `root_folder_id`：** 在 Google Drive 网页打开目标文件夹，URL 中 `folders/` 后面的字符串即为 folder ID。

### 4. 验证

```bash
docker compose exec hermes rclone ls gdrive:
```

应该能看到目标文件夹中的文件列表。

## Docker 侧配置

`docker-compose.yml` 中三个 hermes 容器都设置了：

```yaml
environment:
  - RCLONE_CONFIG=/opt/data/rclone/rclone.conf
```

配置文件通过 `~/.hermes:/opt/data` 的现有 volume mount 自动出现在容器的 `/opt/data/rclone/rclone.conf`。

## 用法

Hermes 容器内：

```bash
# 列出文件夹中的论文
rclone ls gdrive:

# 上传论文 PDF
rclone copy /path/to/paper.pdf gdrive:

# 删除论文
rclone deletefile gdrive:paper.pdf

# 查看存储用量
rclone about gdrive:
```

`gdrive:` 被 `root_folder_id` 限定到目标文件夹，所有路径相对于该文件夹。

## Token 过期

rclone 使用 refresh token 自动续期，无需手动重新授权。如果 refresh token 失效（极少发生），重新执行 `rclone authorize` 并更新 `~/.hermes/rclone/rclone.conf` 中的 token 行。

## 文件布局

```
~/.hermes/rclone/
├── rclone.conf          # rclone 配置（OAuth token + folder ID），chmod 600
├── client_secret.json   # Google OAuth client secret（授权后不再需要，可删除）
└── gdrive-sa.json       # Service account key（已弃用，可删除）
```

## Volume mount 问题（背景）

最初尝试通过 volume mount 直接把 Google Drive 路径挂入容器：

```
${HOME}/Google Drive/.../<目标文件夹>:/opt/data/papers
```

容器内可以写入文件，但 Google Drive 的 File Provider 会在宿主机侧拒绝读取容器创建的文件的**内容**（`Operation not permitted`）。原因：File Provider 不信任非 Apple API 进程创建的文件。`ls` 可以看到文件名和元数据，但 `cat`、`cp` 等需要读取内容时会失败。因此改为 API 方式。
