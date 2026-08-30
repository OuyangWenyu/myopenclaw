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

### 为什么以前「常规方式」配不上

不是配置问题：Microsoft 已**全面停用**个人 Outlook 的 IMAP/SMTP 基础认证——密码、应用密码全部失效，只剩 OAuth 2.0 一条路。而 himalaya 自带的 OAuth2（keyring 变体，见官方 `config.sample.toml`）需要浏览器回调 `localhost` 随机端口——Docker 容器里做不到。所以本仓库把授权交给 [ortie](https://github.com/pimalaya/ortie)（与 himalaya 同作者，专为无浏览器场景设计 device grant），himalaya 只消费现成 token：

```
ortie    →  授权 + 自动刷新 access token（token 落在 ~/.hermes/.config/ortie/tokens/）
himalaya →  XOAUTH2 读/发信（官方 access-token.cmd 变体调用 ortie）
```

默认走 **IMAP/SMTP**（不是 Microsoft Graph），客户端用 Mozilla 注册的 Thunderbird 公共应用 ID（全球共用、开源可复用），**无需自己注册 Azure 应用、无需安装任何东西**。Docker 无浏览器，默认 **device grant**：容器打印一组代码，你在手机/电脑打开 https://login.microsoft.com/device 完成登录。

### 配置（项目根 `.env`，换电脑一个 env 走天下）

`EMAIL_OUTLOOK_*` 全部是**非机密**变量（OAuth 模型下没有邮箱密码），直接写在仓库根目录 `.env`，经 docker-compose 注入容器——换电脑时 clone 项目 + 复制 `.env` + `./scripts/start.sh --build` 即拉起。模板见 `.env.example`：

```
EMAIL_OUTLOOK_ADDRESS=you@outlook.com
EMAIL_OUTLOOK_DISPLAY_NAME=Wenyu Ouyang
```

其余变量有合理默认，可不填；**不填 `EMAIL_OUTLOOK_ADDRESS` 则整个功能关闭**。QQ/DLUT 密码账户不同：密码是真机密，仍留在 `~/.hermes/.env`（见上文），不进项目 `.env`。

可选覆盖：

| 变量 | 默认 | 说明 |
|------|------|------|
| `EMAIL_OUTLOOK_GRANT` | `device` | `device`（推荐，无浏览器）或 `authorization-code` |
| `EMAIL_OUTLOOK_CLIENT_ID` | Thunderbird 公共 client | 换自己的 Azure 应用时覆盖 |
| `EMAIL_OUTLOOK_ACCOUNT_NAME` | `outlook` | himalaya/ortie 里的账户 id |
| `EMAIL_OUTLOOK_IMAP_HOST` / `PORT` | `outlook.office365.com` / `993` | |
| `EMAIL_OUTLOOK_SMTP_HOST` / `PORT` | `smtp.office365.com` / `587` | |

然后重建/重启 Hermes 让 entrypoint 写出配置：

```bash
./scripts/start.sh --build    # 首次：镜像需包含 ortie
```

### 一次性授权（唯一人工步骤，每台新机器一次）

```bash
docker compose exec -u hermes -it hermes ortie auth get -a outlook
```

- **device**：按提示打开 https://login.microsoft.com/device，输入代码，用 Microsoft 账号登录并同意 **Thunderbird** 想要访问的 IMAP/SMTP 权限。**在交互式终端里跑**（普通 Terminal，非 IDE 内嵌）：ortie 会自动轮询并在授权后自行结束，无需 `auth resume`；无 TTY 的会话里它会打印 `ortie auth resume -a outlook '<device_code>'` 提示，照做即可（`-a` 必须带，账户未标记 default）。加 `-u hermes` 是让授权交互与后续读取保持同一用户身份（token 写入助手 `ortie-store-token.sh` 本身也会把属主修正为 hermes）。代码约 15 分钟有效，过期重跑命令。
- **authorization-code**：打开打印的 URL；若容器收不到回调，把浏览器最终跳转的地址交给 `ortie auth resume -a <account> <redirect-uri>`。

验证：

```bash
docker compose exec -u hermes hermes ortie token inspect -a outlook
docker compose exec -u hermes hermes himalaya envelope list -a outlook --page-size 5
```

QQ / DLUT 密码账户保持不变，Outlook 是额外账户。Token 由 ortie 自动续期，随 hermes 备份一起进云盘快照——换电脑时可从备份恢复 `~/.hermes/.config/ortie/`，连授权都免了。

配置漂移说明：首次生成后修改 `EMAIL_OUTLOOK_*` 不会自动重写已写入的配置段，启动日志会出现「检测到 EMAIL_OUTLOOK_* 与已写入配置不一致」警告；此时删除 `~/.hermes/.config/{himalaya,ortie}/config.toml` 中对应 `[accounts.<name>]` 段再重启即可应用新值。

## 验证

```bash
docker compose exec hermes himalaya envelope list --page-size 5
```

## 使用方式

直接跟 Hermes 说「查收件箱」「搜来自 xxx 的邮件」「给 xxx 发封邮件」。Outlook 加一句「用 outlook 账户」。

## 授权范围（仅爱玛士）

邮箱能力（QQ/DLUT/Outlook 全部账户）**只授权给默认 profile 爱玛士**。爱码士/道元/finance 的容器里：

- `himalaya` / `ortie` 二进制被拒绝桩替换，调用即返回「🚫 邮箱访问未授权给此 profile（仅爱玛士可用）」
- 邮箱配置与 token 路径被恒空卷遮蔽（`email-config-none` / `email-tokens-none`），token 对其不可见
- `EMAIL_*` 环境变量不注入、entrypoint 不生成任何邮箱配置

你本人仍可 `docker compose exec`（root）进入受限容器操作邮箱——门禁针对 agent 行为，人是超管。要调整授权范围：改 `docker-compose.yml` 中对应服务的遮蔽卷与 `EMAIL_OUTLOOK_*` 注入，并重建镜像。
