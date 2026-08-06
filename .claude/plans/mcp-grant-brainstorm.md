# MCP 服务分发机制 — 头脑风暴（brainstorm-ideas-existing）

> 2026-08-06 · 来源：把语雀 MCP 分发给 tianyi / 爱码士时产生的"逐 agent 写配置太重复"讨论
> 现状基线：feat/tianyi-bot 已合并 PR #59（yuque MCP bootstrap + skill + docs）

## 机会

- **目标**：把 MCP 服务（语雀为例）"给予"指定 agent，消除逐 agent 写配置的重复
- **痛点**：MCP 服务 × agent 是 N×M 手动接线。每次加服务要改 2-5 个文件，格式各异
- **期望结果**：加服务 = 声明一次 + 指定给谁；加 agent = 声明订阅；配置漂移减少

## 现状事实（粒度矩阵）

| Agent | 配置位置 | 格式 | 粒度 | "给指定 agent" |
|---|---|---|---|---|
| OpenClaw 虾酱 | `~/.openclaw/openclaw.json` | JSON | bot 级 | ✅ |
| OpenClaw tianyi | `~/.openclaw-tianyi/`（render-config.mjs 渲染） | JSON | bot 级 | ✅ |
| OpenClaw zhixun | `~/.openclaw-zhixun/`（独立网络 zhixun-bot-net） | JSON | bot 级 | ✅ |
| Claude Code 飞总 | `~/.claude/settings.json` | JSON | 实例级 | ✅（单实例） |
| **Hermes 全部 profile** | `~/.hermes/config.yaml`（四容器共享 `${HOME}/.hermes:/opt/data`） | YAML | **实例级** | ❌ 无法只给爱码士 |

- 关键结论：**Hermes 的 mcp_servers 是实例级共享**。给爱码士 = 给爱玛士/finance/道元。
- Hermes 侧可差异化的是：skill 挂载（docker-compose 按容器挂）+ SKILL.md 使用规则。
- yuque 是远程 SSE + Bearer key（客户端无 YUQUE_TOKEN），不在 docker-compose 服务清单里。

## 三视角想法（15 个）

### PM（价值/战略）
1. 服务订阅制：目录 + 每 agent 声明订阅，加服务从改 N 处降到 1 处
2. 能力分级：读写权限与 agent 职责对齐，授权是产品决策非配置劳动
3. 灰度发布：新 MCP 先给一个 agent 试点，配置即发布开关
4. 单一事实源防漂移：避免 zhixun/tianyi 配置不兼容事故重演
5. 知识类默认开、工具类默认关

### Designer（体验）
6. 一张声明表：`mcp-registry.yaml` 两段（services/agents），人眼可读可 diff
7. 一键脚本 + dry-run：`add-mcp.sh yuque --for tianyi,coder` 先预览将改文件
8. 连接可视：mcp list / 健康页展示 agent↔service 连线
9. 失败可诊断：提示"未授权给此 agent"而非神秘 401
10. 渐进披露：默认只见"我在用什么"

### Engineer（技术）
11. compose 即注册表：docker-compose.yml 已是服务事实源，渲染器直接读
12. 模板+渲染器统一生成：推广 tianyi render-config.mjs 模式（Hermes YAML / OpenClaw JSON / settings.json 统一渲染）
13. MCP 网关 + per-agent token：统一入口中心化授权，给 = 发 token
14. add-mcp.sh 轻量脚本：不建系统，脚本懂各格式，改完 git diff 展示
15. skill/策略层差异化：接受 Hermes 粒度粗，用 skill 挂载 + 规则分流

## Top 5 优先级

1. **add-mcp.sh 统一分发脚本**（MVP，当天可落地）
   - why：最快解决眼前 yuque 分发（tianyi json 模板 + hermes config.yaml）
   - 假设：agent <10 时脚本可维护；配置 schema 稳定
2. **声明式注册表 + 渲染器**（正解）
   - why：一劳永逸；与 render-config.mjs / entrypoint-wrapper 注入模式同构
   - 假设：各框架 schema 可程序化生成；Hermes config.yaml 安全合并
3. **compose 即事实源**（②的实现路径）
   - why：服务清单本就在 compose，避免第二事实源
   - 假设：所有服务进 compose（远程 SSE 的 yuque 需 external 段）
4. **skill/策略层差异化**（Hermes 粒度修正）
   - why：爱码士/爱玛士共享 mcp_servers，拆配置不如 skill 分流
   - 假设：服务无敏感副作用；严格隔离则必须拆实例配置
5. **MCP 网关 + per-agent token**（远期备选）
   - why：最正确授权模型，与远程 SSE+Bearer 契合
   - 假设：各框架 MCP client 支持自定义 headers；值得网关复杂度

## 推荐路径

- **MVP**：add-mcp.sh（泛化 bootstrap_hermes.sh，支持 OpenClaw json 模板）→ 一条命令分发 yuque 给 tianyi + Hermes
- **中期**：注册表 + 渲染器，替换手改；Hermes 接受实例级粒度，差异化靠 skill
- **远期（可选）**：MCP 网关，仅当 zhixun 接入或需要严格隔离

## 待拍板

1. "给爱码士"是否要求"不给爱玛士"？（决定 Hermes 侧是否拆配置 / 研究 profile 级 mcp_servers）
2. 注册表是否加 external 段收纳远程 SSE 服务（yuque）？
