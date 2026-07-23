# Task Checklist: Yuque MCP Integration

**Plan**：`tasks/plan.md`
**Status**：IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation pending

## Phase 0: Compatibility Gate

- [x] **Task 0：验证 Hermes SSE、Bearer Header 与持久化配置**
  - Acceptance：当前 Hermes 能以正确 Bearer 凭据发现精确 6 个工具；错误/缺失凭据失败；Spec 回写真实配置格式。
  - Verify：Docker/Compose 版本、Hermes MCP 列表、服务日志敏感信息检查。
  - Files：`.claude/specs/yuque-mcp-integration.spec.md`
  - Depends on：None

## Phase 1: Service Foundation

- [x] **Task 1：建立语雀集成契约测试骨架（RED）**
  - Acceptance：静态契约齐全；容器测试默认跳过；首次失败只对应未实现语雀功能。
  - Verify：Git Bash 语法检查与预期 RED 运行。
  - Files：`scripts/test-yuque-mcp-integration.sh`
  - Depends on：Task 0

- [x] **Task 2：固定上游依赖并声明安全配置变量**
  - Acceptance：新克隆 checkout 固定 SHA；已有仓库不被覆盖；模板无真实凭据。
  - Verify：临时目录克隆、偏离仓库保护、secret-like 扫描。
  - Files：`scripts/clone-deps.sh`, `.env.example`, `scripts/test-yuque-mcp-integration.sh`
  - Depends on：Task 1
  - Evidence：固定 SHA 已更新为上游真实 commit `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6`，包含 non-root、`compare_digest`、UID/GID 映射、root fail-closed 与 Windows bind mount 宿主机权限模式。

- [x] **Task 3：增加可选 Compose 服务与本地持久化**
  - Acceptance：profile、localhost、内网、凭据安全失败、两个挂载均符合 Spec；默认流程不变。
  - Verify：默认/显式 profile Compose 解析与契约测试。
  - Files：`docker-compose.yml`, `scripts/start.sh`, `scripts/test-yuque-mcp-integration.sh`
  - Depends on：Tasks 1–2

### Checkpoint A

- [x] Tasks 0–3 全部通过。
- [x] 默认服务集合增加语雀服务，`YUQUE_TOKEN` 未进入 Hermes。
- [x] 用户批准进入 Hermes 自动配置。

## Phase 2: Hermes Consumer Path

- [x] **Task 4：实现 Hermes MCP 幂等注册**
  - Acceptance：认证连接、幂等、保留用户配置、失败可恢复，不通过关闭认证降级。
  - Verify：fixture 配置测试、重建工具发现、Hermes 环境隔离检查。
  - Files：`docker/hermes/entrypoint-wrapper.sh`, 可选配置 helper, `docker-compose.yml`, 集成测试脚本
  - Depends on：Checkpoint A

- [x] **Task 5：增加 `yuque-knowledge` Skill 并验证调用边界**
  - Acceptance：6 工具及语义边界准确；备份需要明确意图；Skill 安装幂等。
  - Verify：静态 Skill 契约、Hermes 真实 MCP loader、五条确定性 mock 用户旅程；真实模型分析部署后人工只读验证。
  - Files：`skills/yuque-knowledge/SKILL.md`, Hermes entrypoint, Compose, 集成测试脚本
  - Depends on：Task 4

### Checkpoint B

- [x] Hermes 认证发现 6 个工具。
- [x] MCP 配置和 Skill 重建恢复、重复运行均幂等。
- [x] 用户批准进入收尾验证。

## Phase 3: Documentation and Validation

- [x] **Task 6：编写运维文档并同步入口**
  - Acceptance：配置、命令、数据、安全边界、无健康检查和故障排查准确。
  - Verify：文档链接、命令一致性与敏感信息扫描。
  - Files：`docs/yuque-mcp.md`, `docs/index.md`, 集成测试脚本
  - Depends on：Checkpoint B

- [x] **Task 7：完成容器、安全、持久化与回归验证**
  - Acceptance：认证、工具发现、内网、重建持久化、默认回归和安全检查全部有证据。
  - Verify：完整静态/容器测试、`git diff --check`、敏感信息和范围审查。
  - Files：集成测试脚本；必要时仅证据驱动修订 Spec
  - Depends on：Tasks 5–6

## Final Gate

- [x] PRD/Spec 自动化 Acceptance Criteria 全部映射到证据。
- [x] 不含健康检查、真实 Token/正文、远程依赖或默认云备份。
- [x] 真实语雀 API 验证状态准确标注为部署后人工验证、当前未执行。
- [x] 自动化与部署后人工验证边界准确标注，未把真实模型分析标为自动化通过。
- [x] 上游 Required 修复已形成 commit，并已同步更新固定 SHA、Spec、依赖脚本与测试。
- [x] 三轮 Spec/Code/Security Review 完成，Critical 为零；pin Required 已解决。
- [ ] 等待用户明确授权 commit、push 和 PR。
