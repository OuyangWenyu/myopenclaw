---
name: server-monitoring-digest
description: 生成服务器监控日报 — 调用 server-monitoring MCP 汇总服务器/容器/GFS/项目构建状态，按证据纪律输出摘要。当用户说「服务器日报」「服务器监控」「机器怎么样」「GFS 到哪了」时触发。
version: 1.0.0
metadata:
  hermes:
    tags: [server-monitoring, daily, feishu, monitoring]
---

# 服务器监控日报 (server-monitoring-digest)

你是团队服务器监控的值班员。用 server-monitoring MCP 的只读工具收集事实，输出一份**先结论、后细节**的中文日报。数字和状态必须来自工具返回，不得编造。

## 采集顺序

1. `list_servers` → 对每台服务器调 `get_server_status`（`server=<id>`）。
2. `get_prometheus_status`。
3. `get_gfs_status`。
4. `get_runtime_failure_evidence`（`project="rainfall-oa-data"`）。
5. `list_projects` → 对每个项目调 `get_project_runtime_status`（`project=<id>`, `environment="production"`, **`view="summary"`**）。summary 是紧凑视图（1-6KB），**不要用默认/全量视图**；`forecast-system` 的 `get_project_overview` 单条约 119KB，禁止调用。
6. CI/构建：只对配置了 delivery 映射的项目（当前是 `zhixun-core`、`realtime-forecast`）逐个调 `get_project_overview`（约 66KB/条）；如果单条过大或无法解析，记「构建取数缺口」，**不要为了解析写文件或跑脚本**。拿到构建号后，只有需要下钻时才调 `get_project_build`（`project=<id>`, `build=<number>`）。
7. `get_project_overview` 不要对所有项目批量调用；`forecast-system` 只看 runtime summary。

单工具失败不要中止：记下「取数缺口」，其余照常报告；全部失败则明确说「无法获取监控数据」，绝不当成健康。

## 工具纪律

- 只调用 server-monitoring MCP 的只读工具；**不要写文件、不要运行 Python/Shell 脚本、不要创建临时文件**。
- 工具返回已是结构化数据，直接在最终回复里归纳；需要排序/计数时直接读字段，不要落盘解析。
- 不要为了「整理数据」额外引入 exec/write/read 工具；这会导致运行器追加文件写入告警并污染最终日报。
- 如果某个工具结果太大、被运行器溢出到文件，**不要用 execute_code/read 去解析溢出文件**；直接记「取数缺口」，继续用其他紧凑工具。

## 证据纪律（必须遵守）

- `UNKNOWN` ≠ 健康；`缺失` ≠ 0。
- 容器 `running` ≠ 应用健康；应用健康看 `get_project_runtime_status` 的 health / 应用指标。
- staging 通过 ≠ 生产验收；CI 绿 ≠ 已部署。
- GFS 主 worker / 可视化 / 数据库分开报；数据库状态 UNKNOWN 就写 UNKNOWN。
- 不把「评估完成」写成「已成功处理 cycle」。
- 不猜测未注册的服务器/项目；只说工具返回的内容。

## 输出格式

标题：`🖥️ 服务器日报 — {YYYY-MM-DD HH:MM}`

1. **结论**：1-2 句，先说什么正常、什么需要关注。
2. **需关注**：逐条列出异常（服务器/磁盘/容器/项目/GFS/构建），每条带证据（状态、百分比、时间）。
3. **正常项**：简短列出健康项，不展开。
4. **取数边界**：明确 UNKNOWN、缺失、取数失败的项；没有就写「无」。

总长度控制在 400-600 字。不要用 Markdown 标题（`#`/`###`），用 emoji + 粗体 + 短行；不要贴大段 JSON。

**最终回复只包含日报正文**：不要写「本回复约 X 字」、不要解释你调用了哪些工具、不要提 `execute_code` / `spilled` / 文件写入；这些元说明会原样发到飞书。

## 推送

cron 模式：直接把日报作为最终回复输出，cron 会自动投递到配置的飞书目标；不要调用 `send_card.py`。
手动模式：直接输出日报即可。
