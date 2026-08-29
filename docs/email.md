# 邮件 (himalaya)

Hermes 通过 [himalaya](https://github.com/pimalaya/himalaya) CLI 工具管理邮件。himalaya v1.2.0 已预装在 Hermes 镜像中。Outlook / Microsoft 365 走 [ortie](https://github.com/pimalaya/ortie) v2.2.0 做 OAuth 2.0（Microsoft 已停用密码认证）。

> **重要**：Hermes **不把 email 当消息平台**（不会自动回复邮件）。email 仅作为 CLI 工具手动使用。

## 配置

首次启动时 entrypoint 自动从 `~/.hermes/.env` 解析 `EMAIL_*` 变量并生成 `~/.hermes/.config/himalaya/config.toml`。

以 QQ 邮箱为例，在 `~/.hermes/.env` 中配置（**保持注释状态**）：

```
# EMAIL_ADDRESS=你的QQ号@qq.com
# EMAIL_PASSWORD=授权码
# EMAIL_IMAP_HOST=imap.qq.com
# EMAIL_IMAP_PORT=993
# EMAIL_SMTP_HOST=smtp.qq.com
# EMAIL_SMTP_PORT=587
```

注意事项：

1. 不要用个人主力邮箱，建一个新邮箱或用小号
2. SMTP 端口必须用 **587**（STARTTLS），不能用 465
3. `EMAIL_*` 变量必须保持注释状态 — 取消注释会导致 Hermes 把 email 当作消息平台
4. QQ 邮箱需要开启 IMAP/SMTP 服务并生成授权码

## 添加第二个邮箱（密码认证）

在 `~/.hermes/.env` 中追加 `EMAIL2_*` 变量：

```
# EMAIL2_ADDRESS=wenyuouyang@dlut.edu.cn
# EMAIL2_PASSWORD=你的密码
# EMAIL2_IMAP_HOST=mail.dlut.edu.cn
# EMAIL2_IMAP_PORT=993
# EMAIL2_SMTP_HOST=mail.dlut.edu.cn
# EMAIL2_SMTP_PORT=465
# EMAIL2_ACCOUNT_NAME=dlut
# EMAIL2_DISPLAY_NAME=Wenyu Ouyang
```

多账户使用：`himalaya envelope list -a dlut`，不加 `-a` 使用默认账户。

## 添加 Outlook / Microsoft 365（OAuth）

Microsoft 个人/企业邮箱已停用 IMAP/SMTP 密码认证，必须走 OAuth 2.0。本仓库拆成两层：

```
ortie  →  授权 + 刷新 access token（token 落在 ~/.hermes/.config/ortie/tokens/）
himalaya → XOAUTH2 读/发信（access-token.cmd 调用 ortie）
```

默认用 **IMAP/SMTP**（不是 Microsoft Graph），客户端是 Thunderbird 已验证的公共应用，无需自己注册 Azure 应用。Docker 无浏览器，默认 **device grant**：容器打印一组代码，你在手机/电脑打开 https://microsoft.com/devicelogin 完成登录。

在 `~/.hermes/.env` 中追加（同样保持注释）：

```
# EMAIL_OUTLOOK_ADDRESS=you@outlook.com
# EMAIL_OUTLOOK_ACCOUNT_NAME=outlook
# EMAIL_OUTLOOK_DISPLAY_NAME=Wenyu Ouyang
# EMAIL_OUTLOOK_GRANT=device
```

可选覆盖：

| 变量 | 默认 | 说明 |
|------|------|------|
| `EMAIL_OUTLOOK_GRANT` | `device` | `device`（推荐，无浏览器）或 `authorization-code` |
| `EMAIL_OUTLOOK_CLIENT_ID` | Thunderbird 公共 client | 换自己的 Azure 应用时覆盖 |
| `EMAIL_OUTLOOK_IMAP_HOST` / `PORT` | `outlook.office365.com` / `993` | |
| `EMAIL_OUTLOOK_SMTP_HOST` / `PORT` | `smtp.office365.com` / `587` | |

然后重建/重启 Hermes 让 entrypoint 写出配置：

```bash
./scripts/start.sh --build    # 首次：镜像需包含 ortie
```

一次性授权（在容器内，交互式）：

```bash
docker compose exec -it hermes ortie auth get -a outlook
```

- **device**：按提示打开 https://microsoft.com/devicelogin，输入代码，用 Microsoft 账号同意 IMAP/SMTP 权限。
- **authorization-code**：打开打印的 URL；若容器收不到回调，把浏览器最终跳转的地址交给 `ortie auth resume <redirect-uri>`。

Outlook 网页设置里还需开启 IMAP：设置 → 邮件 → 转发和 IMAP。

验证：

```bash
docker compose exec hermes ortie token inspect -a outlook
docker compose exec hermes himalaya envelope list -a outlook --page-size 5
```

QQ / DLUT 密码账户保持不变，Outlook 是额外账户。Token 随 hermes 备份一起进云盘快照。

## 验证

```bash
docker compose exec hermes himalaya envelope list --page-size 5
```

## 使用方式

直接跟 Hermes 说「查收件箱」「搜来自 xxx 的邮件」「给 xxx 发封邮件」。Outlook 加一句「用 outlook 账户」。
