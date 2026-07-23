# Implementation Plan: 将语雀 MCP 服务接入 myopenclaw，供 Hermes 使用

**Issue**：[GitHub #51](https://github.com/OuyangWenyu/myopenclaw/issues/51)
**PRD**：`.claude/prds/yuque-mcp-integration.prd.md`
**Spec**：`.claude/specs/yuque-mcp-integration.spec.md`
**Branch**：`codex/yuque-mcp-integration`
**Status**：IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation pending

## Overview

按“研发日报”的本地托管模式，把固定 commit 的 `yuque_mcp_server` 作为可选 Compose 服务接入 myopenclaw，并让 Hermes 通过 Docker 内网和 Bearer 认证调用上游 6 个 MCP 工具。计划优先消除 Hermes SSE/Header 配置的不确定性，再以测试先行的增量完成依赖固定、服务编排、凭据隔离、持久化、Hermes 配置、Skill 和文档。

本计划修改 `myopenclaw`，并包含已获授权的上游 `yuque_mcp_server` Required 安全修复；不添加健康检查，不使用真实语雀正文做自动化测试。

## Planning Constraints

- 当前工作树已有大量用户改动；每个任务只编辑和暂存明确列出的路径。
- 未经用户明确要求，不 commit、不 push、不创建 PR。
- Docker 已可用；自动化容器验证使用隔离 Compose project，不重建或干扰用户当前的 Hermes/backup-cron。
- Hermes compatibility gate 未通过前，不实现自动配置，也不关闭 Bearer 认证绕过问题。
- 真实语雀 Token 测试需要用户单独授权；mock/contract 测试与真实验证分开报告。
- 所有 Bash 运行验证使用用户实际的 Git Bash，不使用 WSL `bash.exe` 冒充验证。

## Architecture Decisions

1. **默认服务**：`yuque-mcp` 使用 Compose profile `yuque`，由 `scripts/start.sh` 显式启用并与 Hermes、backup-cron 一起启动。
2. **可复现依赖**：记录来源分支，构建固定到完整 commit `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6`；该提交包含 non-root、`compare_digest`、UID/GID 映射、root fail-closed 与 Windows bind mount 宿主机权限模式；已有仓库只核对，不自动 reset/clean。
3. **凭据最小化**：`YUQUE_TOKEN` 只进入 `yuque-mcp`；Hermes 只获得 MCP Bearer 凭据。
4. **安全失败**：cloud/SSE 模式缺少 `YUQUE_TOKEN` 或 `MCP_API_KEY` 时，在 Python 服务启动前退出非零。
5. **本地持久化**：快照挂载到 `~/.myagentdata/yuque-mcp/change-data`，备份挂载到 `~/.myagentdata/yuque-mcp/backups`；默认不进入云备份。
6. **本机端口**：只绑定 `127.0.0.1:${YUQUE_MCP_PORT:-18000}:18000`；Hermes 使用 Docker DNS。
7. **无健康检查**：不增加 `/health`、Uptime Kuma monitor、Compose healthcheck 或周期性 MCP initialize。
8. **风险优先**：先实测 Hermes SSE URL、Header 与持久化配置格式，再选择最小幂等配置实现。

## Dependency Graph

```text
Task 0 Hermes compatibility gate
  └─> Task 1 contract test harness (RED)
       ├─> Task 2 pinned dependency + env contract
       └─> Task 3 optional Compose service + persistence
             └─> Checkpoint A
                  └─> Task 4 Hermes MCP provisioning
                       └─> Task 5 yuque-knowledge Skill
                            └─> Checkpoint B
                                 ├─> Task 6 operator docs
                                 └─> Task 7 container/security/regression validation
                                      └─> Final review gate
```

Task 2 和 Task 3 在契约稳定后理论上可并行，但二者都修改同一测试脚本，实际执行保持顺序以避免共享文件冲突。其余任务具有明确依赖，顺序执行。

## Task Details

### Task 0: 验证 Hermes SSE、Bearer Header 与持久化配置

**Description:** 在不调用语雀 API 的前提下，用占位 `YUQUE_TOKEN` 和随机本地 `MCP_API_KEY` 启动固定版本服务，确认当前 Hermes 镜像实际支持的 SSE endpoint、Authorization Header 字段、配置持久化位置和工具发现方式。将证据和最终最小配置格式回写 Spec。这是后续实施的阻断门禁。

**Acceptance criteria:**

- [x] 记录当前 Hermes 镜像/CLI 版本及实际配置文件路径。
- [x] 正确 Bearer 凭据能 initialize 并发现精确 6 个工具；错误或缺失凭据不能连接。
- [x] Spec 中不再保留 SSE/Header 字段格式的不确定描述，且没有调用真实语雀 API。

**Verification:**

- [x] `docker version` 与 `docker compose version` 成功。
- [x] 隔离 Hermes 容器消费 helper 配置并显示 `yuque-mcp` 及 6 个工具（依赖例外：不重建用户当前 Hermes）。
- [x] 检查服务日志，不包含测试 key、`YUQUE_TOKEN` 或正文。

**Dependencies:** None
**Files likely touched:** `.claude/specs/yuque-mcp-integration.spec.md`
**Estimated scope:** S（1 文件 + 运行时验证）

### Task 1: 建立语雀集成契约测试骨架（RED）

**Description:** 新增单一 Bash 测试入口，先编码 PRD/Spec 的静态契约和可选容器测试开关。测试在功能尚未实现时应因缺少语雀配置而失败，并给出精确失败项；不得读取真实 `.env` 值或正文。

**Acceptance criteria:**

- [x] 测试覆盖固定 commit、profile、localhost 端口、凭据隔离、两个持久化挂载、6 个工具、无健康检查和默认不启用。
- [x] 容器测试默认跳过，只有显式测试开关才执行。
- [x] 初次运行失败原因仅指向尚未实现的语雀契约，不被现有无关脏文件干扰。

**Verification:**

- [x] `D:\Git\bin\bash.exe -n scripts/test-yuque-mcp-integration.sh`
- [x] `D:\Git\bin\bash.exe scripts/test-yuque-mcp-integration.sh` 得到预期 RED 汇总。

**Dependencies:** Task 0
**Files likely touched:** `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** S（1 文件）

### Task 2: 固定上游依赖并声明安全配置变量

**Description:** 扩展依赖脚本和 `.env.example`。新克隆 checkout 固定 commit；已有仓库只核对并报告偏离。配置模板只加入变量名、安全说明和非秘密默认值。

**Acceptance criteria:**

- [x] 新环境能从指定来源 ref checkout 完整固定 commit。
- [x] 已有仓库存在本地改动或不同 HEAD 时不被覆盖，并输出当前/目标 SHA。
- [x] `.env.example` 包含 `YUQUE_TOKEN`、`MCP_API_KEY`、`YUQUE_MCP_PORT`、`YUQUE_CHANGE_RETENTION_DAYS`，不含可用凭据。

**Verification:**

- [x] 固定 SHA 由 clone 脚本静态契约和现有上游 HEAD 精确核对。
- [x] 偏离仓库保护逻辑经 fixture/静态契约核对，工作树不被自动 reset/clean。
- [x] Task 1 中依赖与环境变量测试转绿；secret-like 扫描通过。

**Dependencies:** Task 1
**Files likely touched:** `scripts/clone-deps.sh`, `.env.example`, `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** M（3 文件）

### Task 3: 增加可选 Compose 服务与本地持久化

**Description:** 增加 profile 为 `yuque` 的服务、localhost 端口、Docker 内网、资源限制、安全失败启动命令和两个持久化挂载；只在显式启用路径中准备宿主机目录。保持默认 Compose 行为不变。

**Acceptance criteria:**

- [x] `yuque-mcp` 使用 `../yuque_mcp_server/Dockerfile`，加入 `myopenclaw-net`，只绑定 localhost。
- [x] 缺少任一必要凭据时服务退出非零；错误不输出值。
- [x] 默认 Compose 不启动语雀 profile，且语雀目录不进入云备份配置。

**Verification:**

- [x] `docker compose config` 和 `docker compose --profile yuque config` 均可解析。
- [x] 默认启动脚本服务集合与显式 profile 服务列表符合预期。
- [x] Task 1 中 Compose、端口、隔离、持久化和无健康检查测试转绿。

**Dependencies:** Tasks 1–2
**Files likely touched:** `docker-compose.yml`, `scripts/start.sh`, `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** M（3 文件）

## Checkpoint A: 服务基础完成

- [x] Tasks 0–3 的验收和验证全部通过。
- [x] `scripts/start.sh` 默认显式启用语雀服务，且不启动其他 myopenclaw 服务。
- [x] `YUQUE_TOKEN` 未进入 Hermes 配置或环境。
- [x] 固定 commit、端口、持久化和无健康检查契约有自动化证据。
- [x] 用户已批准进入 Hermes 自动配置。

### Task 4: 实现 Hermes MCP 幂等注册

**Description:** 根据 Task 0 的真实配置格式，实现最小、结构化、可恢复的 MCP 注册。只在显式启用时添加 `yuque-mcp`；保留所有用户已有配置。若需要辅助脚本，使用镜像已有运行时，不新增不必要依赖。

**Acceptance criteria:**

- [x] URL 使用 Docker DNS，Bearer 凭据可用，Hermes 不持有 `YUQUE_TOKEN`。
- [x] 连续执行两次配置结果字节级或结构级等价，不产生重复 server/toolset。
- [x] 配置失败不覆盖原文件；关闭 Bearer 认证不是允许的 fallback。

**Verification:**

- [x] fixture 配置的添加、重复运行、已有同名项、禁用清理和异常输入测试通过。
- [x] 隔离 Hermes 容器消费 helper 配置后发现 `yuque-mcp` 及精确 6 个工具。
- [x] 容器环境检查确认 Hermes 中不存在 `YUQUE_TOKEN`。

**Dependencies:** Checkpoint A
**Files likely touched:** `docker/hermes/entrypoint-wrapper.sh`, `docker/hermes/configure-yuque-mcp.py`（仅在 Task 0 证明确有必要时）, `docker-compose.yml`, `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** M（3–4 文件）

### Task 5: 增加 `yuque-knowledge` Skill 并验证调用边界

**Description:** 新增 Hermes Skill、只读挂载和幂等链接。Skill 只做工具选择与语义约束，不复制上游 HTTP、备份或快照代码。

**Acceptance criteria:**

- [x] Skill 准确覆盖 6 个工具、TOC/100 篇边界、最小正文读取和相邻快照语义。
- [x] `backup_repo` 只在用户明确要求备份时调用，并禁止同库并发/无意义重复。
- [x] 重建或重复启动后 Skill 只存在一次，现有 Skill 不被覆盖。

**Verification:**

- [x] frontmatter、工具名、风险规则和禁止项静态测试通过。
- [x] Linux lifecycle fixture 验证托管 Skill 链接可见、幂等且安全清理。
- [x] 使用 mock 工具返回完成五条确定性用户旅程的 prompt/行为检查，不调用真实语雀 API；真实 Hermes 模型分析留作部署后人工只读验证。

**Dependencies:** Task 4
**Files likely touched:** `skills/yuque-knowledge/SKILL.md`, `docker/hermes/entrypoint-wrapper.sh`, `docker-compose.yml`, `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** M（4 文件）

## Checkpoint B: Hermes 消费路径完成

- [x] Hermes 能以认证方式发现 6 个工具。
- [x] Skill 安装和 MCP 配置重复运行均幂等。
- [x] 五条必需用户旅程通过 mock/contract 验证；Hermes 真实 MCP loader 已在隔离容器发现 6 工具。
- [x] 没有真实正文、Token、远程地址或健康检查进入 diff。
- [x] 用户已批准进入收尾验证。

### Task 6: 编写运维文档并同步入口

**Description:** 编写维护者文档，说明固定依赖、`.env` 变量、选择性构建启动、localhost 端口、数据目录、工具边界、无健康检查现状和故障排查；从文档索引建立入口。

**Acceptance criteria:**

- [x] 文档命令可直接执行，明确 Git Bash/PowerShell 和 Docker 前提。
- [x] 明确区分容器状态、MCP 工具发现和真实语雀功能验证。
- [x] 明确语雀数据默认不进入 Git 或云备份，并说明安全删除/保留边界。

**Verification:**

- [x] 文档链接检查通过，命令与 Compose service/profile 名称一致。
- [x] 文档不包含真实 Token、正文、内部 IP 或已删除的健康检查方案。

**Dependencies:** Checkpoint B
**Files likely touched:** `docs/yuque-mcp.md`, `docs/index.md`, `scripts/test-yuque-mcp-integration.sh`
**Estimated scope:** M（3 文件）

### Task 7: 完成容器、安全、持久化与回归验证

**Description:** 执行完整 contract 测试和回归检查。用占位 Token 验证认证、工具发现和重建持久化，不调用语雀 API；如用户另行授权，再单独执行最小真实只读 smoke test。

**Acceptance criteria:**

- [x] 缺失凭据、401、正确 initialize/tools/list、容器内网连通和 6 工具发现全部有证据。
- [x] fixture 快照与备份在容器重建后保留；默认 profile 和现有服务回归通过。
- [x] diff/镜像层/普通日志不包含 Token、正文、内部地址或备份文件。

**Verification:**

- [x] `D:\Git\bin\bash.exe scripts/test-yuque-mcp-integration.sh`
- [x] 显式容器测试开关下运行同一脚本，全部 contract tests 通过。
- [x] `git diff --check`、敏感信息扫描和仅限本功能路径的 diff 审核通过。
- [x] 最终报告明确标注“真实语雀 API 未验证”。

**Dependencies:** Tasks 5–6
**Files likely touched:** `scripts/test-yuque-mcp-integration.sh`, `.claude/specs/yuque-mcp-integration.spec.md`（仅在实证要求修订契约时）
**Estimated scope:** S（1–2 文件 + 运行验证）

## Final Checkpoint: Ready for Review

- [x] PRD 与 Spec 的自动化 Acceptance Criteria 均有测试证据。
- [x] 全部新增/修改文件符合范围，没有覆盖既有用户改动。
- [x] 默认启动、研发日报、Hermes 其他 Skill 和 OpenClaw 行为不受影响。
- [x] 没有新增健康检查、定时任务、远程依赖或云备份。
- [x] 静态测试、容器 contract、持久化重建和安全扫描分别报告。
- [x] 真实语雀验证状态被准确标注为部署后人工只读验证、当前未执行。
- [x] 三轮 Spec/Code/Security Review 已完成。
- [ ] 等待用户明确授权 commit/push/PR。

## Milestone Mapping

| PRD Milestone | Plan Tasks |
|---|---|
| 1. 可复现依赖与服务编排 | Tasks 1–3 |
| 2. 安全配置与持久化 | Tasks 2–4, 7 |
| 3. Hermes MCP 与 Skill | Tasks 0, 4–5 |
| 4. 集成验证与文档 | Tasks 6–7 |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Hermes 不支持带 Header 的远程 SSE MCP | High | Task 0 阻断性验证；失败则回到 Spec/Issue 决策，不关闭认证 |
| Docker 不可用导致关键验证缺失 | High | Task 0 前置检查；不可用时不进入 Task 1 实施 |
| 已有上游仓库与固定 commit 不同且含本地改动 | High | 只报告偏离，不自动切换、reset 或 clean |
| `docker-compose.yml` 与现有未提交改动重叠 | High | 修改前后按具体 hunk 审核，只追加语雀范围 |
| Bearer key 被日志或配置测试泄露 | High | 测试随机临时 key、禁止回显、日志和 diff 扫描 |
| 备份/快照进入现有云备份通配范围 | High | 显式排除验证，检查备份脚本实际包含范围 |
| `backup_repo` 误调用导致限流或磁盘增长 | Medium | Skill 仅在明确意图时调用，禁止并发和主动重复 |
| 无健康检查导致故障发现较晚 | Medium | 接受 MVP 决策；以显式错误和诊断命令处理，不私自增加监控 |

## Parallelization

- 可并行分析：Task 2 的依赖脚本设计与 Task 3 的 Compose 设计。
- 实际修改顺序执行：Tasks 1–7 都共享契约测试或 Compose/entrypoint 文件。
- 不安排子代理并行实施，除非用户后续明确要求。

## Open Questions

无新的产品问题。自动化验证边界已确认采用研发日报方式，上游 Required 修复和固定 SHA 已同步完成。仅剩部署后真实语雀 API 与 Hermes 模型分析的人工只读验证，未执行前不得宣称真实集成通过。

---
*Status: IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation remains pending; no commit/push is authorized in this run.*
