# Plan: 清理 Repo Scanner 全家桶

**Source Issue**: #41 Python Decouple
**Branch**: `feat/issue-41-python-decouple`
**Complexity**: Low (纯删除 + 3 处小修改 + 1 次集成验证)

## Summary

删除 myopenclaw 中与 `git-contribution-stats` + `repo-scanner-mcp` 功能重复的 Repo Scanner 全家桶（采集/查询/推送链路），共 ~2200 行 Python + 相关基础设施。清理后仓库活动日报通过 `skills/daily-dev-report/` → MCP → DeepSeek → Feishu 路径继续工作。

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| 删除方式 | git rm | 直接删除，不留死代码 |
| docker-compose | `docker-compose.yml` | volume mount 行级删除 |
| entrypoint | `docker/hermes/entrypoint-wrapper.sh` | 按块删除，保留注释分隔 |

## Files to Change

### 🗑 删除 (13 files)

| 文件 | 原因 |
|------|------|
| `scripts/collect-repos.py` | 采集 → `git-contribution-stats/scripts/collect.py` |
| `scripts/repo-summary.py` | 查询 → `git-contribution-stats/core/report.py` 或 MCP |
| `scripts/repo-triage-send.py` | LLM+飞书 → Hermes `daily-dev-report` skill 已用 MCP 替代 |
| `scripts/tests/test_collect_repos.py` | 随源文件 |
| `scripts/tests/test_query_repo_data.py` | 随源文件 |
| `scripts/tests/test_repo_triage_send.py` | 随源文件 |
| `skills/repo-triage/SKILL.md` | repo-triage skill 主体 |
| `skills/repo-triage/tools/query_repo_data.py` | Hermes 数据查询工具 |
| `skills/repo-triage/tools/send_card.py` | Feishu 卡片（另有 3 副本，此项删除不影响） |
| `scripts/launchd/ai.myopenclaw.collect-repos.plist.template` | launchd 模板 |
| `scripts/launchd/ai.myopenclaw.repo-triage.plist.template` | launchd 模板 |
| `scripts/launchd/install-collect-repos.sh` | install 脚本 |
| `scripts/launchd/install-repo-triage.sh` | install 脚本 |
| `scripts/install-repo-triage-cron.sh` | Hermes cron 安装脚本 |
| `configs/repos.toml` | 仓库列表配置（仅 collect-repos.py 使用） |

### ✏️ 修改 (3 files)

| 文件 | 改动 |
|------|------|
| `docker-compose.yml` | 删除 L22, L80 的 `./skills/repo-triage:/opt/hermes-skills/repo-triage:ro` volume mount |
| `docker/hermes/entrypoint-wrapper.sh` | 删除 L100-104 repo-triage symlink 块 |
| `skills/daily-dev-report/SKILL.md` | L72 修复 `email_name_mapping.csv` 路径（当前引用 `/opt/hermes-skills/repo-triage/../`） |

## Tasks

### Task 1: 删除 Repo Scanner Python 脚本 + 测试
- **Action**: `git rm` 5 个 Python 文件 + 3 个测试文件
- **Files**: `scripts/collect-repos.py`, `scripts/repo-summary.py`, `scripts/repo-triage-send.py`, `scripts/tests/test_collect_repos.py`, `scripts/tests/test_query_repo_data.py`, `scripts/tests/test_repo_triage_send.py`

### Task 2: 删除 repo-triage skill 目录
- **Action**: `git rm -r skills/repo-triage/`

### Task 3: 删除基础设施文件
- **Action**: `git rm` launchd plist 模板、install 脚本、Hermes cron 脚本、repos.toml

### Task 4: 清理 docker-compose.yml
- **Action**: 删除 hermes 和 hermes-coder 两个 service 中的 `./skills/repo-triage:/opt/hermes-skills/repo-triage:ro` volume mount

### Task 5: 清理 entrypoint-wrapper.sh
- **Action**: 删除 repo-triage symlink 块（L100-104）

### Task 6: 修复 daily-dev-report SKILL.md
- **Action**: 修复 `email_name_mapping.csv` 的引用路径，不再依赖 `/opt/hermes-skills/repo-triage/`

### Task 7: 验证 — 飞书 Hermes 私聊推送
- **Action**: 确认 `skills/daily-dev-report/tools/send_card.py` 工作正常
- **Validate**: 
  ```bash
  # 重启 Hermes 确认无报错
  docker compose restart hermes
  docker compose logs hermes --tail 20
  # 确认 repo-triage symlink 安装日志消失
  docker compose logs hermes | grep -i "repo-triage"
  # 确认 daily-dev-report MCP 数据通路正常
  docker compose exec repo-scanner-mcp python3 -c "from core.report import daily_report_as_dict; print(daily_report_as_dict())"
  ```

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| 宿主 launchd 有残留 plist | 中 | 需手动 `launchctl unload`，脚本提醒 |
| email_name_mapping.csv 路径断裂 | 低 | daily-dev-report SKILL.md 中修正路径 |
| docker compose 启动因 volume 不存在的旧镜像报错 | 低 | `--build` 或 `up -d` 重启即可 |

## Acceptance

- [ ] 所有 15 个文件已删除
- [ ] docker-compose.yml + entrypoint 已清理
- [ ] `docker compose restart hermes` 无报错
- [ ] repo-scanner-mcp MCP 数据通路正常（`daily_report_as_dict()` 返回数据）
- [ ] daily-dev-report 的 send_card.py 路径引用正确
