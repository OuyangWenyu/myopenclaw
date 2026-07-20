# Plan: 文档审视 — 移除 myloop + 新增 AgentOps 页面 + 细节修复

**Source**: 合并 main 后的全量文档审视
**Complexity**: Medium
**Type**: Docs + scripts — 不改 Dockerfile / docker-compose.yml

## 需求重述

1. **移除 myloop** — myopenclaw 就是最高级项目，不再有 myloop 作为上层设计项目。所有 myloop 引用（README、CLAUDE.md、docs、scripts）全部清理。
2. **AgentOps 独立页面** — `collect_agentops.py` 是独立运转的健康信号采集器（容器重启检测、备份过期、磁盘使用率、网关错误循环），和 daily-dev-report 无关。需要单独文档。
3. **细节修复** — 服务数量、端口缺失、脚本名称错误、gateway 错误检测缺文档。

## 变更范围

### Phase 1: 移除 myloop

| 文件 | 动作 | 说明 |
|------|------|------|
| `docs/myloop-integration.md` | **DELETE** | 整页删除 |
| `README.md` | UPDATE | 移除能力表 myloop 行、生态图 myloop 节点、设计原则行、clone-deps 提及 |
| `CLAUDE.md` | UPDATE | 移除「MyLoop Integration」整节（~40 行）、entrypoint 描述中的 symlink 说明、架构规则、skills 目录注释 |
| `mkdocs.yml` | UPDATE | 移除 nav 中 MyLoop 条目，调整「集成」分组 |
| `docs/index.md` | UPDATE | 移除 MyLoop 链接 |
| `docs/portability.md` | UPDATE | 移除依赖图中的 myloop、软依赖表 myloop 行、不受管理内容中的 myloop 行 |
| `scripts/clone-deps.sh` | UPDATE | 移除 myloop 克隆步骤 |
| `scripts/start.sh` | UPDATE | 移除依赖检查中的 `~/code/myloop` |
| `scripts/collect_agentops.py` | UPDATE | 输出路径从 `~/code/myloop/memory/agentops-ledger/inbox.md` → `~/.myagentdata/agentops/inbox.md` |
| `scripts/launchd/install-collect-agentops.sh` | UPDATE | 更新输出路径提示 |
| `docker/claude-code/entrypoint.sh` | UPDATE | 移除 myloop skills symlink 代码块（~15 行） |

### Phase 2: 新增 AgentOps 文档

| 文件 | 动作 | 说明 |
|------|------|------|
| `docs/agentops.md` | **CREATE** | 独立页面：5 种信号检测、阈值配置、调度（launchd 7:45）、输出路径、手动触发 |
| `mkdocs.yml` | UPDATE | nav 新增 AgentOps 条目（运维分组） |
| `docs/index.md` | UPDATE | 首页新增 AgentOps 链接 |
| `docs/monitoring.md` | UPDATE | 补充 gateway 错误循环检测（`check-gateway-errors.sh`），从 AgentOps 页面交叉引用 |

### Phase 3: 细节修复

| 文件 | 动作 | 说明 |
|------|------|------|
| `docs/architecture.md` | UPDATE | 「11 个」→「12 个」；数据服务表补充端口列 |
| `docs/monitoring.md` | UPDATE | `setup-uptime-kuma-monitors.py` → `setup-uptime-kuma.sh`；新增 gateway 错误检测小节 |

## 设计决策

### myloop 移除后的影响

- **晨间三签 (morning-triage)**: 已改为 Hermes cron skill（`skills/morning-triage-v2/`），不依赖 myloop。无需改动。
- **AgentOps 采集**: 唯一的 myloop 耦合是输出路径。改为 `~/.myagentdata/agentops/inbox.md`，与其他 myagentdata 数据统一。
- **Skill 加载机制**: myloop skills symlink 逻辑从 `entrypoint.sh` 移除。当前 morning-triage-v2 已在 repo 内 `skills/` 目录，不受影响。
- **clone-deps.sh**: 不再克隆 myloop。依赖仓库从 4 个减为 3 个（aisecretary、git-contribution-stats、dailyinfo）。

### AgentOps 页面风格

遵循现有 docs 风格：
- 功能说明开头
- 表格 + 代码块组合
- 前置条件 → 配置 → 命令 → 故障排查 的标准结构

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `collect_agentops.py` 输出路径变更后 morning-triage 读不到 | 晨间三签缺少 AgentOps 信号 | morning-triage-v2 通过 TDAI memory 搜索关键词，不直接读文件。验证 skill 中 AgentOps 关键词匹配逻辑不受影响 |
| entrypoint.sh myloop 逻辑移除影响其他功能 | 容器启动行为变化 | 仅移除 symlink 块，不触及配置初始化、API key 映射等核心逻辑 |

## 验收

- [ ] `grep -rn 'myloop\|myLoop\|MyLoop' --include='*.md' --include='*.sh' --include='*.py' --include='*.yml'` 除 `.claude/prds/` 历史文档外无匹配
- [ ] `docs/myloop-integration.md` 已删除
- [ ] `docs/agentops.md` 存在且内容完整
- [ ] `uv run mkdocs build --strict` 通过
- [ ] `mkdocs.yml` nav 无死链
