# 扫码新增 Hermes

飞书扫一次码，就在这台机器上多一个只负责聊天的 Hermes。它不进现有四个 profile，也不写进主栈的 `docker-compose.yml`。

个人栈仍然是爱玛士、爱码士、道元、finance，共用 `~/.hermes`。扫码创建的助手各自一份目录，compose 项目名是 `hermes-bind`。

## 扫一次

先确认这台机器上有三样东西（都不是本功能新引入的依赖）：

- Node.js ≥ 18（页面用了 `fetch`）
- `uv`（脚本内部用它跑 Python）
- 仓库根 `.env` 里的 `DEEPSEEK_API_KEY`（必需；`GLM_API_KEY` 可选，只是兜底模型）

SDK 装一次，版本由 `services/agent-bind` 的 lockfile 锁定，不要装到全局 Node：

```bash
npm ci --prefix services/agent-bind
node scripts/feishu_register_app.mjs
```

浏览器打开 `http://127.0.0.1:8766/`。页面只听本机回环，二维码大约 10 分钟、只能用一次。用飞书扫它，确认页只有「新建应用」。扫码的飞书账号要能新建企业自建应用（管理员或被授权），否则确认页会被拒绝。

扫成功后，同一个进程做两件事：

1. 把这个助手写到 `~/.hermes-agents/<id>/`。
2. 执行 `docker compose -f ~/.hermes-agents/compose.yml -p hermes-bind up -d`，只拉这一个容器。

页面变成「已连上，可以私聊」时，脚本已经在容器日志里看到飞书长连接。成功页保留 10 分钟，终端 Ctrl-C 可提前结束。应用密钥只进该目录权限 600 的 `.env`，页面和终端日志都不出现。

不要用 `scripts/start.sh` 拉这个助手，也不要在仓库目录里裸跑 `docker compose up`。那两步管的是主栈。

同一个应用编号再扫一次，不会再建第二份目录，只会保证对应容器在跑。飞书没有返回 `open_id` 时，目录不会创建。

## 连不上怎么办

`up -d` 之后脚本会轮询容器日志，最多 90 秒，看到 `connected to wss://` 才算成功。超时或容器起不来时：

1. 停掉这次启动的容器；
2. 把 `~/.hermes-agents/<id>/bind.json` 的 `status` 写成 `failed`，并记下 `failure_reason`；
3. 页面显示失败原因，进程退出码 1。

排查用 `docker logs hermes-bind-<id>`，修好后重新扫码即可（同一应用不会建第二份目录，容器也会被重新拉起）。

端口 `8766` 被占用时脚本直接退出，不再进入扫码流程；占用进程用 `lsof -nP -iTCP:8766 -sTCP:LISTEN` 查。

默认权限只到聊天：收发私聊、群里被 @ 时回答、读群名与被引用消息，外加一条**只写**的表情回复权限。更早创建的助手只申请了收消息与发消息：日志里会持续出现 `99991672`（缺 `im:chat:readonly` / `im:message:readonly`），而且**收到的消息上不会出现「打字中」表情**——Hermes 用消息表情做回执，缺 `im:message.reactions:write_only` 时那次调用只在 DEBUG 级别报错，界面上看起来就是「它没收到」。到开发者后台按日志里的链接补权限即可，不用重建容器；补完权限要发一个新版本，表情回执才会生效。

## 它能做什么

默认只聊天。配置在 `agents/hermes-bind/defaults.yaml`。

| 有 | 没有 |
|---|---|
| 自己的目录，挂到容器 `/opt/data` | `~/.hermes`、`~/.myagentdata` |
| `memory.provider: builtin`（v0.23 的内置实现；`local` 不再合法） | 共享长期记忆 |
| 扫码人是唯一白名单；群里要 @ 才回答 | 空白名单（那是配对模式，谁都能说话） |
| 模型 `deepseek-flash`，兜底 `zai/glm-5.3-flash` | 邮箱、GitHub、GitCode、飞书 CLI |
| 2G 内存、1 个 CPU，加入 `myopenclaw-net` | 看板派发、论文、事务库，以及个人栈里那些团队能力客户端 |
| 卡片和公式开关与现有 Hermes 相同 | 宿主端口 |

两把模型钥匙只从仓库根 `.env` 读取：`DEEPSEEK_API_KEY`、`GLM_API_KEY`。

## 两个 profile 与网关

扫码助手的数据目录里有两个 profile：**根 profile**（`config.yaml`，镜像首次启动时生成）和 **owner**（`profiles/owner/`）。容器只给 owner 起独立网关：

- `profiles/owner/config.yaml` 里写 `gateway.standalone: true` —— v0.23 要求 per-profile 网关显式 opt-in，否则 `gateway run --profile owner` 会以 `exit 78` 拒绝启动（配置还必须有 `_config_version`，缺了同样 `exit 78`）
- 飞书凭据（`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_ALLOWED_USERS` 等）写在 **`profiles/owner/.env`**，不写容器级 `.env` —— 容器级 env 对根 profile 的网关同样可见，它会拿同一个 app_id 去连，把 owner 挤掉（`feishu_app_lock`）
- 所以占住飞书长连接的是 owner；根 profile 的网关仍在跑，但拿不到凭据，也就不会去连接

同一个应用只能有一条长连接。隔离清扫原本按主栈的四个网关槽设计（要等满 4 个槽才动手），扫码容器只有 `gateway-default` + `gateway-owner` 两个，因此日志里会出现「等满 900s 只见到 2 个」的告警，约 15 分钟后 root 槽才会被收掉——这不影响聊天，只是多一个空转进程。

## 停和删

停容器：

```bash
docker compose -f ~/.hermes-agents/compose.yml -p hermes-bind stop
```

飞书里的应用已经启用时，开发者后台的「删除应用」是灰的。用创建者账号打开飞书管理后台，进入 **工作台 → 应用管理**，找到该应用，在配置里把它从已启用拨到停用。回到开放平台的 **凭证与基础信息**，底部的删除才会可点。`~/.hermes-agents` 留在磁盘上，不会因为停容器被删掉。

## 备份

`hermes/scripts/backup.sh` 把 `~/.hermes-agents` 另存到 `$BACKUP_ROOT/hermes-agents/`。`.env` 和 `secrets/` 不进快照。对话和 `config.yaml` 在快照里。换一台机器要重新扫码，应用密钥不会从云盘恢复。详见 [备份系统](backup.md)。
