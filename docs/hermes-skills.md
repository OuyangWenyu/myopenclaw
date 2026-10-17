# Hermes Skill 机制与接线现状

> 最后更新：2026-10-13（晨间简报日期误判修复已同步回仓库）

本页说明 myopenclaw 里 **Hermes skill 是怎么被加载执行的**，以及仓库 `skills/` 与它的关系。一句话版本：**live 执行源是 `~/.hermes/skills` 下的原生副本（agent 自维护）；仓库 `skills/` 是版本源/参考并提供只读挂载——两者当前没有自动同步，「改仓库版 ≠ 改线上行为」。**

## 事实基线（2026-10-10 实测）

- `hermes skills list` 中本仓库相关的 skill（morning-briefing / morning-triage-v2 / daily-dev-report / yuque-daily-digest 等）**source 全部为 local**——加载自 `~/.hermes/skills/**` 原生副本。
- `~/.hermes/config.yaml` 的 `skills.external_dirs` 只注册了两个目录（aisecretary、`/opt/mylibrary-skills`）；**仓库 `skills/` 挂载到的 `/opt/hermes-skills/` 不在其中**——挂载存在（docker-compose 对 hermes / hermes-coder 各有 5 条 `:ro` 挂载），但**不参与 skill 发现**。
- 原生副本由 **agent 会话自行创建与修订**（frontmatter 常见 `author: hermes-agent`；Hermes 配置了 `creation_nudge_interval`，会主动推动从会话里沉淀技能）。历史版本被 agent 移入 `~/.hermes/skills/.archive/`（如 `yuque-daily-digest-dupe-20260921`、`yuque-daily-digest-symlink-20261009`、`morning-briefing-symlink-20260921`）。
- 历史上把仓库版接入发现的机制是 **symlink**：`~/.hermes/skills/<name>` → `/opt/hermes-skills/<name>`。该机制与原生副本**反复同名撞车**（`Skill name collision` → cron 静默跳过技能加载、`last_status` 仍显示 ok、run 输出顶部有 `Skill(s) not found and skipped`），agents 按手册多次把 symlink 移出到 `.archive/`。**重建该 symlink 的来源 2026-10-12 查明：`docker/hermes/entrypoint-wrapper.sh` 里的 morning-briefing 接线块（PR #16 时代遗留，容器每次启动重放）**——当日晨间简报预加载再次被拒后定位，该块已删除；其余三个块（morning-triage-v2 / daily-dev-report / yuque-daily-digest）有意保留，morning-briefing 此后只走原生副本。

## 2026-10-13 同步：晨间简报「日期误判成周末」修复

当日 07:30（北京）那期晨间简报被代理**凭直觉**断言「今天是周末」吞掉（零工具调用，7 秒收工；旧 cron 表达式 `0-5` 还让它在周六也跑，session 里躺着上一期合法的周末话术）。修复已落地 live，并**同步回仓库**作为版本源：

| 位置 | 内容 |
|---|---|
| `~/.hermes/skills/devops/morning-briefing/SKILL.md`｜仓库 `skills/morning-briefing/SKILL.md` | 第 0 节硬规则：星期几**只能**来自 `scripts/today.py`，`--check-briefing-day` 返回 0 就必须发；事故记录在案 |
| 同上 `scripts/today.py` | 按北京时间（UTC+8）输出日期/星期/`is_weekend`（事实字段）/`is_briefing_day`（闸门：周一至周六），容器 UTC 不算数 |
| `~/.hermes/scripts/cst_today.py`｜仓库 `skills/morning-briefing/scripts/cst_today.py` | cron `--script` 预脚本（cron 只认 `~/.hermes/scripts/` 下的路径），stdout 每轮注入 prompt；薄壳转发给上面那个 `today.py`，避免两处实现漂移 |
| cron job `260cdd03f97e` | 表达式 `30 23 * * 0-5`（北京周一–周六 07:30）、`script: cst_today.py`、prompt 含「第一步：先查日期」全文 |
| `start.sh` 种子 | 新部署：预脚本**只补缺、不覆盖**（`~/.hermes` 是权威副本）；job 不存在才创建，prompt 与 `--script` 从那时起一致 |

**2026-10-17 口径变更**：晨间简报改为**周一至周六**推送（周六汇总周五信息），仅周日休息；cron 表达式恢复 `0-5`，日期闸门字段由 `is_weekend` 改为 `is_briefing_day`（`--check-briefing-day`）。10-13 事故的根因（凭直觉判周末）已由硬规则 + 日期预脚本封死，并由 `tests/test_morning_briefing_today.py` 钉住周六=简报日、周日=休息日。

⚠️ 同步方向仍是**单向人工**（live → 仓库）：仓库是版本源/种子，`~/.hermes` 是 live。仓库版完整镜像仍需人工搬运 —— 引用到的 `unread_report.py` / `fetch_imap_unread.py` / `mark_read.py` 只在原生副本里（`unread_report.py` / `list_unread.py` 的展示串仍含真实邮箱地址，**不得**进公开仓库）。

**凭据卫生（2026-10-13 整改）**：`mark_read.py` 原先硬编码 qq/dlut 明文口令，已改为经 `fetch_imap_unread.account_creds()` 从 **himalaya 配置**读（entrypoint 由 env 生成，单一来源）；同日移出 `~/.hermes/scripts/morning_briefing.py`（孤儿脚本，同含明文）与 5 份陈旧副本（`workspace/`、`tmp/` 下的旧拷贝与一次性调试探针，能力均已被正式脚本覆盖）。守卫：`skills/morning-briefing/test-cleanup.sh`（8 项，含 live/仓库两处脚本目录的明文检查）。

**界线**：口令本就存放在 himalaya 配置里、随备份进**个人云盘**（设计如此，合理）；红线是**公开仓库**（GitHub）—— 仓库镜像与上述守卫确保公开侧零凭据。本机其他散落副本属整洁问题，不构成额外暴露。

## 已知差距与影响

| 差距 | 说明 |
|---|---|
| 改仓库 skill 不生效 | 编辑 `skills/<name>/` 只更新版本源与挂载内容；线上行为由原生副本决定 |
| 两份副本漂移 | 最典型是 `yuque-daily-digest`：原生版（v1.2.0，含 `scripts/daily_digest.py` 直连 SSE 的 fallback + 大量实测排障规程——agent 维护的"作战手册"）vs 仓库版（自包含、无脚本，由 `skills/yuque-daily-digest/test-skill.py` 静态断言钉住）。**改哪一份会生效，取决于你改的是哪一份** |
| 挂着 ≠ 生效 | `yuque-knowledge` 仓库有源、容器有挂载，但 `hermes skills list` 里**没有它**——当前没有任何一端加载它 |
| 同名 symlink 再现 | 触发 skill 加载跳过（**静默**）。排障第一步：`ls -ld ~/.hermes/skills/<name>*`，发现与原生撞名的项移入 `~/.hermes/skills/.archive/`，**不要放回**。morning-briefing 的重建源头（wrapper 接线块）已于 2026-10-12 移除；若再出现，先确认容器用的不是旧镜像 |

## 排障与操作

```bash
# 看真实加载面（source=local / builtin / external）
docker compose exec hermes /opt/hermes/.venv/bin/hermes skills list | grep <name>

# 看原生副本与历史档案
ls ~/.hermes/skills/ ~/.hermes/skills/.archive/ | grep <name>

# 判断某次 cron 是否被静默跳过：run 输出顶部找
#   ⚠️ Skill(s) not found and skipped: <name>

# 改"线上 skill 行为" = 改原生副本；改仓库版仅更新版本源（除非未来收敛为一条线）
docker compose exec hermes cat /opt/data/skills/<category>/<name>/SKILL.md
```

## 待决（治理方向，未定；决定前动 skill 先按上表查真实加载面）

把两个世界收敛为一条线，可选：

1. **注册 `/opt/hermes-skills` 进 `skills.external_dirs` 并清退原生副本**——仓库成为唯一源；需先处理与 agent 自维护习惯的冲突及历史同名撞车。
2. **在 `start.sh` 把仓库 `skills/` 同步（拷贝）到 `~/.hermes/skills`**——原生侧变派生物（对 openclaw 侧 skill，`start.sh` 已有拷贝式安装的先例）。
3. **明确"原生副本是 live、仓库只做参考"**——按现状固化到文档与流程（当前本页的诚实写照）。

## 相关

- [语雀知识库接入](yuque-mcp-hermes.md) —— `yuque-daily-digest` 的原生副本详情（脚本 fallback、HOST 逻辑地址）
- [调度系统](scheduling.md) —— cron job 与其排障
