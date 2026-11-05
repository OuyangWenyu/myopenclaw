# 2026-11-02 健康通知消费者部署验收

观测时间：2026-11-02T15:01:01+08:00。按用户要求，先部署到 81，机器人由用户后续指定。

## 部署身份

- 主机：`<MONITOR_HOST>`；容器：`server-health-notifier`。
- 模式：`observe-only`；`delivery_enabled=false`。容器持续检查 MCP，不启用飞书投递。
- 镜像：`sha256:ffcc885e7b1f31e6099d21f7c618d703c9569f3b286e5f212882ac515c505967`。
- 构建源码归档 SHA256：`800ebfd89c8c685c44fc1bf84e6968ea4c31a6e03386cf7e3f716ecfa5547a20`，包括 8 个源码/构建/示例文件，不含凭据。
- 部署时来源为 myopenclaw 本地开发工作树；本 PR 纳入对应源码及验收记录。已部署镜像仍以源码清单、归档哈希和镜像 ID 标识，不将提交时间误当成部署时间。
- 独立 Compose project：`myopenclaw-server-health-notifier`。部署和持久化目录位于服务器 `/opt/myopenclaw/server-health-notifier`；未部署或重启整套 myopenclaw。

## 构建与运行验收

初次在线构建和 host 网络构建均无法访问 PyPI。随后在本机 WSL Linux 从官方 PyPI 下载固定 `mcp==1.28.1` 及其依赖，共 29 个 Python 3.12/Linux wheel，逐文件记录 SHA256。81 采用 `Dockerfile.offline` 在 `--network=none` 下安装并执行 `pip check`，未更改宿主现有 Python 环境、依赖版本约束或 Docker 全局网络配置。

| 检查 | 结果 |
|---|---|
| 实际 SDK | 镜像内 mcp==1.28.1 |
| MCP URL | `https://<MCP_DOMAIN>:8765/mcp`，系统 TLS 验证开启 |
| 认证 | 使用专用 server-health-notifier Bearer key，沿用已注册凭据 |
| 真实只读联调 | 镜像内 `--dry-run` 成功 |
| 持续运行 | 连续两轮成功心跳；Docker healthy |
| watcher | CURRENT、fresh=true、64 个实体、采集失败 0 |
| 飞书投递 | 未启用；未注入飞书应用/群机器人凭据；发送数 0 |
| 消费状态 | 未创建 state.sqlite，未推进消费游标，未创建投递任务 |
| 运行隔离 | UID/GID 3001、只读根文件系统、移除 capabilities、no-new-privileges、资源限制 |
| 其他服务 | 其他运行容器 ID 与监控服务 key 注册表摘要保持不变 |

运行时使用 host 网络访问现有 MCP；不监听额外端口。只有该消费者的数据目录可写，当前仅保存观察模式心跳。key 在构建完成后由服务器读取专用登记项注入运行配置，未进入镜像或源码归档。

本地相关回归：Linux 154 项通过；Windows 153 项通过，另有一项 POSIX 符号链接测试在 Linux 覆盖。原 issue-notifier 回归保留。上述心跳健康仅表示 MCP 观察链路正常，不能据此宣称飞书投递已通过。

## 后续操作

用户指定飞书应用或群机器人后，再配置其凭据及已指定的测试群，用清晰标注测试的虚拟异常/恢复卡片验收投递。测试卡片不应通过制造生产故障或向监控库插入假事件生成。

初始阶段仅观察，没有发送飞书消息；随后用户提供应用凭据并确认沿用测试群，已完成下述投递验收和正式消费切换。

完整配置、恢复与消费规则见[健康通知说明](server-health-notifier.md)。

## 飞书应用配置与正式投递启用

验收观测时间：2026-11-02T15:48:16+08:00。用户提供应用身份并指定既有测试群，应用 ID、App Secret、群 ID 均未写入仓库、源码归档或镜像。

- 应用模式：app。凭据通过加密 SSH stdin 输入，保存在服务器受限运行配置 `feishu.env`（root 0600）；原监控 key 文件保持不变。
- 使用实际镜像的卡片构建器、独立临时 SQLite outbox 和共享飞书发送器，向指定群发送两张标题带“测试验收”的虚拟异常/恢复卡片。
- 飞书 API 确认接收 2 张，发送失败 0、剩余待发 0、死信 0。未通过制造生产故障或向监控库插入假事件测试。此验收确认 API 接受，不代替人工阅读确认。
- 切换容器命令至正式 daemon，`delivery_enabled=true`、`mode=delivery`；实际生效的独立 Compose 配置为该发布目录中的 `compose.delivery.json`，原 `compose.json` 保留为只读观察回退配置。
- 连续两轮正式消费心跳通过，Docker healthy，监控来源健康，watcher CURRENT/fresh，覆盖 64 个实体，采集失败 0。
- 首次静默基线：cursor=0；待发 0、死信 0，没有补发观察期间历史。正式轮询尚未遇到真实健康跃迁事件；测试卡片和真实生产事件分别记录。
- 未重新构建镜像，其他服务容器 ID 和监控 key 注册表摘要保持不变。
- 后续新确认的健康异常/恢复由该消费者自动投递。机器人响应式对话仍由原 Hermes 接入负责，通知链路不经过 LLM。

对应源码和本验收记录随本 PR 交付；创建或合并 PR 不会自动更新已部署容器。
