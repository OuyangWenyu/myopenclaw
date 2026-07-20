# 邮件 (himalaya)

Hermes 通过 [himalaya](https://github.com/pimalaya/himalaya) CLI 工具管理邮件。himalaya v1.2.0 已预装在 Hermes 镜像中。

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

## 添加第二个邮箱

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

## 验证

```bash
docker compose exec hermes himalaya envelope list --page-size 5
```

## 使用方式

直接跟 Hermes 说「查收件箱」「搜来自 xxx 的邮件」「给 xxx 发封邮件」。
