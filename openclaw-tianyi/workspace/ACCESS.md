# 团队能力接入指南（给自备 agent 的成员）

> 面向**想把自己常用 agent 接上团队能力**的成员。只想最快用上？**零配置**——飞书里私聊或 @ 天一 就行，天一背后连着同样的能力。

## 三条路径，挑一条

| 路径 | 适合谁 | 要做的事 |
|---|---|---|
| ① 直接用天一（零配置） | 所有人 | 飞书里 @天一 或私聊提问 |
| ② 自己的 agent 接团队能力 | 想在 WorkBuddy / 千问办公 / 豆包工作 / DeepSeek Harness / Claude Code / Codex 里用 | 把「服务速查」里的 MCP 端点 + token 粘进你的 agent |
| ③ 服务器上自部署 agent | —— | 暂不提供（出现真实需求后统一做）；先用 ①② |

## 服务速查（MCP）

三项能力都是 **MCP 服务**，任何支持 MCP 的 agent 都能接：

1. **服务器监控** — 服务器/容器状态、GFS 处理进度、各项目 CI 构建情况
   - 端点：`https://10.48.0.81:8765/mcp`（streamable-http + Bearer，内网/VPN 可达）
   - token 向管理员获取；CA 证书随公开仓库提供（`docker/tianyi-bot/ca/server-monitoring-ca.pem`）或问天一索取
2. **语雀知识库** — 团队语雀文档查询、变更记录
   - 端点：团队服务器部署完成后公布（问天一或管理员）
   - token 向管理员获取
3. **仓库动态** — GitHub/GitCode 提交统计、研发日报、成员活跃度
   - 端点：同上；token 向管理员获取

> ⚠️ token 是团队资产：只放进你自己的工具配置，勿转发到公开场合。401 = token 不对或已轮换，找管理员换新的。

## 各 agent 怎么接（傻瓜版）

- **WorkBuddy / 千问办公 / 豆包工作 / DeepSeek Harness**：在「设置 → MCP / 工具 / 扩展」里添加自定义 MCP 服务器，把端点与 `Authorization: Bearer <token>` 填进去（菜单名以各工具版本为准）。**也可以把本页整段复制给你的 agent，让它自己配。**
- **Claude Code**：`claude mcp add --transport http <名字> <端点> --header "Authorization: Bearer <token>"`
- **其他支持 MCP 的客户端**：同理（streamable-http + Authorization header）。

各服务的详细文档（部署、验收、FAQ）在各自仓库（GitCode `dlut-water` 组织）里；找不到就问天一或管理员。

## 接完先自检

- **最省事**：直接用你的 agent 试一条查询（如"看下当前有哪些服务"）——能答上就是通了。
- **要机器级确认**：member-check 自检工具 —— 公开仓库 `OuyangWenyu/myopenclaw` 的
  `docs/member-check.md`（需克隆该仓库；命令 `./scripts/member-check.sh`，Windows 只需 Docker）。
  拿不到仓库也没关系：让天一或管理员帮你确认。

## FAQ

- **401 / 鉴权失败**：token 不对或已轮换 → 找管理员要新的。
- **连不通**：内网服务需要 VPN/校园网；再检查端点是否抄全（含 `/mcp` 或 `/sse` 后缀）。
- **工具列表为空**：服务端可能在重启/升级，稍后再试；持续为空找管理员。
- **token 多久轮换**：管理员统一管理，轮换会另行通知。
