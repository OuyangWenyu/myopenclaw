# Plan: myopenclaw 集成 git-contribution-stats MCP — 每日研发贡献报告

**Source PRD**: `.claude/prds/daily-dev-report.prd.md`
**Selected Milestone**: #4 — myopenclaw: docker-compose + Hermes MCP 注册 + skill + cron
**Complexity**: Small

## Summary
git-contribution-stats 已提供 SSE MCP server (port 8001)。myopenclaw 只需三步：docker-compose 加容器 → Hermes 注册 MCP → 写 skill + 配 cron。每条改动都有现成模式可照抄。

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Docker service | `docker-compose.yml:344-358` (aisecretary) | build context + image + restart + ports + volumes + networks + deploy |
| MCP 注册 | `~/.hermes/config.yaml:425-430` (aisecretary) | `mcp_servers.<name>.url` + `platform_toolsets.cli` 加 `mcp-<name>` |
| Hermes skill | `skills/repo-triage/SKILL.md` | frontmatter (name/description/version/metadata.hermes.tags) + 数据采集 + 规则 + 发送 |
| 飞书私聊推送 | `skills/repo-triage/tools/send_card.py` | LARK_CLI_APP_ID/SECRET → Feishu API → LARK_USER_OPEN_ID |
| Cron 注册 | Hermes CLI: `hermes cron create` | `--schedule "55 23 * * *"` (UTC 23:55 = 北京 07:55) |
| Skill 发现 | `docker/hermes/entrypoint-wrapper.sh:93-98` | 检查目录 → mkdir → symlink |

## Files to Change
| File | Action | Why |
|---|---|---|
| `docker-compose.yml` | UPDATE | 新增 `repo-scanner-mcp` service（参照 aisecretary） |
| `skills/daily-dev-report/SKILL.md` | CREATE | Hermes skill 定义 |
| `skills/daily-dev-report/tools/send_card.py` | CREATE | 飞书私聊推送（复用 repo-triage 的 send_card） |
| `docker/hermes/entrypoint-wrapper.sh` | UPDATE | 加 daily-dev-report skill symlink |

### 不在本仓库改动的
| 文件 | 位置 | 说明 |
|---|---|---|
| `~/.hermes/config.yaml` | 宿主机 | MCP 注册 + toolsets（手动执行一次） |
| Hermes cron job | Hermes 内部存储 | 通过 `hermes cron create` CLI 注册 |

## Tasks

### Task 1: docker-compose 加 `repo-scanner-mcp` service
- **Action**: 在 `docker-compose.yml` 末尾新增 service，镜像构建路径指向 git-contribution-stats
- **Mirror**: aisecretary service (docker-compose.yml:344-358)
- **Key config**:
  ```yaml
  repo-scanner-mcp:
    build:
      context: ../git-contribution-stats
      dockerfile: docker/mcp-server/Dockerfile
    image: myopenclaw/repo-scanner-mcp:latest
    container_name: repo-scanner-mcp
    restart: unless-stopped
    ports:
      - "8001:8001"
    volumes:
      - ${HOME}/.myagentdata/repo-scanner:/data:ro
    environment:
      - REPO_SCANNER_DB_PATH=/data/repos.sqlite
    networks:
      - myopenclaw-net
  ```
- **Validate**: `docker compose up -d repo-scanner-mcp` 后容器运行，`curl http://localhost:8001/health` 或 TCP 通

### Task 2: Hermes config.yaml 注册 MCP server
- **Action**: 在 `~/.hermes/config.yaml` 的 `mcp_servers` 下加 `repo-scanner` entry，`platform_toolsets.cli` 加 `mcp-repo-scanner`
- **Mirror**: aisecretary 注册 (config.yaml:425-430) 和 toolsets (config.yaml:456)
- **Config diff**:
  ```yaml
  mcp_servers:
    repo-scanner:
      connect_timeout: 60
      enabled: true
      timeout: 120
      url: http://repo-scanner-mcp:8001/mcp/
  
  platform_toolsets:
    cli:
      - ...existing...
      - mcp-repo-scanner    # ← 加这行
  ```
- **Note**: 这是宿主机上的一次性配置，不在 worktree 代码里。执行后需 `docker compose restart hermes` 生效
- **Validate**: Hermes 重启后 `hermes mcp list` 能看到 `repo-scanner`

### Task 3: 创建 Hermes skill `daily-dev-report`
- **Action**: 新建 `skills/daily-dev-report/SKILL.md`，定义 skill 行为
- **Mirror**: `skills/repo-triage/SKILL.md` 的结构
- **Skill 行为**:
  1. 调 MCP `get_daily_report`（昨日 UTC 日期）获取确定性数据
  2. 若 `has_activity == false` → `[SILENT]`
  3. DeepSeek LLM 润色：主题聚类 + 核心战役总结（1-3 条）+ 每人 1-2 句小结
  4. 飞书推送：通过 `send_card.py` 推送到私聊
- **Prompt 约束**: "基于数据生成摘要，不编造任何信息。用中文。按以下结构输出：整体统计 → 核心战役 → 每人小结 → 仓库详情"
- **Validate**: 容器内 `cat <测试数据> | python3 /opt/hermes-skills/daily-dev-report/tools/send_card.py` 成功推送

### Task 4: 创建 send_card.py（技能工具）
- **Action**: 复制 `skills/repo-triage/tools/send_card.py` 到 `skills/daily-dev-report/tools/send_card.py`
- **Mirror**: 完全相同的代码（LARK_CLI_APP_ID/SECRET → LARK_USER_OPEN_ID 私聊推送）
- **原因**: 每个 skill 自带工具，解耦，避免跨 skill 引用
- **Validate**: 同 Task 3

### Task 5: entrypoint-wrapper 加 skill symlink
- **Action**: 在 `docker/hermes/entrypoint-wrapper.sh` 加 daily-dev-report 的 symlink 注册块
- **Mirror**: repo-triage 的注册块 (entrypoint-wrapper.sh:100-104)
- **Validate**: 重启后 `ls /opt/data/skills/daily-dev-report/SKILL.md` 存在

### Task 6: Hermes cron 注册
- **Action**: 在容器内执行 `hermes cron create "55 23 * * *" "请执行 daily-dev-report 技能，生成昨日研发贡献报告并推送" --name daily-dev-report --skill /opt/hermes-skills/daily-dev-report/SKILL.md`
- **Mirror**: 和 repo-triage cron 相同的注册方式
- **Validate**: `hermes cron list` 显示 `daily-dev-report` job，Next run 为下次 07:55

## Validation
```bash
# 1. docker-compose 拉起 MCP
docker compose up -d repo-scanner-mcp
curl http://localhost:8001/  # 应返回 404 或 SSE 握手（不是连接拒绝）

# 2. Hermes 重启并验证 MCP 可见
docker compose restart hermes
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp list | grep repo-scanner

# 3. skill 文件可访问
docker compose exec hermes ls /opt/data/skills/daily-dev-report/

# 4. 手动触发测试（先确保 SQLite 有昨日数据）
cd ~/code/git-contribution-stats && python3 scripts/collect.py --since 2026-07-19
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>

# 5. 飞书收到私聊消息 → 成功
```

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| MCP server 起不来（依赖路径错误） | Low | High | 先手动 `docker build`+`run` 验证，再写 compose |
| Hermes 重启时 MCP 容器未就绪导致连接失败 | Low | Low | restart: unless-stopped + Hermes 会重试 MCP |
| LLM 输出质量不稳定 | Medium | Medium | skill prompt 约束"不编造信息"，可迭代 |

## Acceptance
- [ ] `repo-scanner-mcp` 容器正常运行
- [ ] Hermes `mcp list` 显示 repo-scanner
- [ ] `daily-dev-report` skill 可被 Hermes 发现
- [ ] 手动触发 cron 后飞书收到日报
- [ ] 日报格式：整体统计 + 每人小结 + 仓库详情
