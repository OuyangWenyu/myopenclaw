# Cross-Platform myopenclaw: Mac + Linux 双平台兼容

## Problem

myopenclaw 目前仅支持 macOS 部署，依赖 macOS 特有的路径约定（`${HOME}` 挂载、`launchd` 定时任务、`brew` 工具链）。实验室成员和其他潜在用户没有 Mac 电脑就无法部署，已有师妹明确反馈被 Mac 门槛挡住。同时社区贡献者（如 #46 的 GitCode CLI 集成）提交了 Linux 部署能力，如果不纳入主线，贡献会漂在 fork 里逐渐腐烂，Mac 版本也享受不到这些增强。

## Evidence

- **#46 `feat/gitcode-cli`**：外部贡献者提交了 GitCode CLI 集成 + 387 行 Linux 服务器部署文档，证明 Linux 部署是真实需求
- **师妹直接询问**：想自己跑 myopenclaw，被告知需要 Mac 电脑后放弃
- **实验室实际需求**：服务器 `10.48.0.81` 上已部署了一套精简版（4 容器），但配置全靠 gaoyu 手搓，无标准化流程
- **Owen 本人未来可能 Linux 部署**：不排除以后在 Linux 环境跑这套系统

## Users

- **Primary**: 实验室同学 / 开源社区用户 — 有 Linux 机器（服务器或个人 PC），想一键部署 myopenclaw 跑起来
- **Secondary**: Owen 自己 — 未来可能在 Linux 环境部署，现在 Mac 上的能力也要持续纳入 Linux 贡献
- **Not for**: 需要全功能 Mac 版对等体验的用户 — MVP 阶段只承诺 PR 里的能力能在 Linux 上跑

## Hypothesis

我们相信 **Mac/Linux 双平台共享同一份 docker-compose.yml 和脚本** 能让 Linux 用户一键拉起 myopenclaw 核心服务，同时 Mac 用户无感知地获得 Linux 贡献者提交的增强能力。

我们怎么知道做对了：
- Linux 上执行 `./scripts/start.sh` 能成功拉起 PR 中包含的服务（hermes + openclaw-gateway + backup-cron）
- Mac 上执行同样的命令行为不变，且新能力（gc CLI、gh auth 修复）正常可用
- 不再有人因为「只有 Mac 能跑」而放弃尝试

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| Linux 一键部署成功率 | `./scripts/start.sh` 在 Ubuntu 24.04 上一次通过 | 手动验证 + CI（如有） |
| Mac 回归 | 现有 8 个服务全部正常启动 | `docker compose ps` 全部 Up |
| 平台差异隔离度 | 所有平台差异收敛到 ≤2 个文件 | 代码审查 |
| 新贡献者接入 | 下个 Linux 相关 PR 无需重写 docker-compose | 跟踪下一个外部 PR |

## Scope

### MVP — 当前两个 PR 的能力在双平台均可一键拉起

**合并 #46 + #50 后**：

| 能力 | Mac | Linux |
|------|-----|-------|
| `gc` CLI（GitCode 操作） | ✅ 从 #46 获得 | ✅ #46 原生支持 |
| gh CLI 认证修复 | ✅ 从 #50 获得 | ✅ #50 原生支持 |
| hermes + openclaw-gateway + backup-cron | ✅ 已有 | ✅ 已有 |
| `docs/linux-server-setup.md` 部署文档 | —（无关） | ✅ 从 #46 获得 |
| `./scripts/start.sh` 一键启动 | ✅ 不变 | ✅ 目标达成 |

**关键原则：Linux 有什么，Mac 就顺便有什么。** 反之不要求——Mac 的 claude-code、launchd 等不强求 Linux 立刻支持。

### Out of scope

- **claude-code 容器 Linux 化** — 依赖复杂（Node.js 版本、cc-connect 路径），暂不做
- **launchd → systemd/cron 迁移** — 定时任务先手动，MVP 不要求自动化
- **tdai-memory / repo-scanner-mcp Linux 适配** — 当前无 Linux 需求，后续按需添加
- **全功能对等** — Mac 上所有 8 个服务全部能在 Linux 跑，这是长期目标不是 MVP
- **CI/CD 双平台测试** — 先手动验证，CI 后续加
- **macOS 特有工具的 Linux 替代方案** — `brew`、`launchd`、`sed` 差异等，遇到再修

## Delivery Milestones

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | 合并 #46 + #50，解决冲突，双平台验证 | `./scripts/start.sh` 在 Mac 和 Linux 上均正常启动核心服务（hermes, openclaw-gateway, backup-cron）；`gc` 和 `gh` 在容器内可用 | in-progress | [plan](./plans/cross-platform-milestone-1.plan.md) |
| 2 | 平台差异隔离 | 所有 `${HOME}` 路径、OS 特定命令收敛到可识别的几个文件，文档化差异清单 | pending | — |
| 3 | Linux 新增服务按需纳入 | 每当有 Linux 贡献者提交新服务能力，Mac 同步吸收；反向亦然 | pending | — |

## Open Questions

- [ ] **云端同步（rclone / .cloud.conf）**：Linux 上的云盘挂载路径和 Mac 不同，`start.sh` 依赖 `.cloud.conf`，Linux 上是必需的吗？还是可以跳过？
- [ ] **哪些 Mac 能力是 Linux 部署的硬依赖**？比如 `.env` 中的 `TZ=Asia/Shanghai` 是通用的，但 `launchd` 定时任务、`brew` 安装的工具在 Linux 上缺失会不会导致 `start.sh` 报错退出？
- [ ] **`sed` / `awk` 兼容性**：macOS 是 BSD 版本，Linux 是 GNU 版本，脚本中是否有 `sed -i` 等不兼容用法？
- [ ] **Docker 版本差异**：Linux 上 Docker 行为（特别是 `host.docker.internal`、卷挂载权限）是否与 Mac 一致？
- [ ] **多架构支持**：是否需要考虑 ARM64（树莓派、云服务器）还是只 x86_64？

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Mac 脚本在 Linux 上因 BSD/GNU 差异报错 | Medium | 部署失败 | 审查所有 `sed`/`awk`/`grep` 用法，优先用 POSIX 兼容写法 |
| `${HOME}` 路径在 Linux 不同用户下行为不一致 | Medium | 数据挂载到错误目录 | #50 已经修了一部分（hermes HOME 变化），合并后额外验证 |
| Docker 网络模式差异（`host.docker.internal` 不存在于 Linux） | Low | 容器间通信失败 | 当前用 `myopenclaw-net` bridge，不依赖 `host.docker.internal` |
| 云盘/备份在 Linux 上路径不可用 | Medium | `start.sh` 退出 | 让 `.cloud.conf` 缺失时不要 fatal error，降级为 warning |
| 合并后 Mac 回归失败 | Low | Owen 的日常服务中断 | 先在 `fix/gh-token-auth` 分支上合 #46，验证通过再合 main |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
