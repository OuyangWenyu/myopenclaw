# Hermes Skill 机制与接线现状

> 最后更新：2026-10-10（issue #79 排查中发现并固化）

本页说明 myopenclaw 里 **Hermes skill 是怎么被加载执行的**，以及仓库 `skills/` 与它的关系。一句话版本：**live 执行源是 `~/.hermes/skills` 下的原生副本（agent 自维护）；仓库 `skills/` 是版本源/参考并提供只读挂载——两者当前没有自动同步，「改仓库版 ≠ 改线上行为」。**

## 事实基线（2026-10-10 实测）

- `hermes skills list` 中本仓库相关的 skill（morning-briefing / morning-triage-v2 / daily-dev-report / yuque-daily-digest 等）**source 全部为 local**——加载自 `~/.hermes/skills/**` 原生副本。
- `~/.hermes/config.yaml` 的 `skills.external_dirs` 只注册了两个目录（aisecretary、`/opt/mylibrary-skills`）；**仓库 `skills/` 挂载到的 `/opt/hermes-skills/` 不在其中**——挂载存在（docker-compose 对 hermes / hermes-coder 各有 5 条 `:ro` 挂载），但**不参与 skill 发现**。
- 原生副本由 **agent 会话自行创建与修订**（frontmatter 常见 `author: hermes-agent`；Hermes 配置了 `creation_nudge_interval`，会主动推动从会话里沉淀技能）。历史版本被 agent 移入 `~/.hermes/skills/.archive/`（如 `yuque-daily-digest-dupe-20260921`、`yuque-daily-digest-symlink-20261009`、`morning-briefing-symlink-20260921`）。
- 历史上把仓库版接入发现的机制是 **symlink**：`~/.hermes/skills/<name>` → `/opt/hermes-skills/<name>`。该机制与原生副本**反复同名撞车**（`Skill name collision` → cron 静默跳过技能加载、`last_status` 仍显示 ok、run 输出顶部有 `Skill(s) not found and skipped`），agents 按手册多次把 symlink 移出到 `.archive/`。**重建该 symlink 的"外部同步"来源尚未查明**（2026-10-10 记录，待观察；发现再现先按下文排查）。

## 已知差距与影响

| 差距 | 说明 |
|---|---|
| 改仓库 skill 不生效 | 编辑 `skills/<name>/` 只更新版本源与挂载内容；线上行为由原生副本决定 |
| 两份副本漂移 | 最典型是 `yuque-daily-digest`：原生版（v1.2.0，含 `scripts/daily_digest.py` 直连 SSE 的 fallback + 大量实测排障规程——agent 维护的"作战手册"）vs 仓库版（自包含、无脚本，由 `skills/yuque-daily-digest/test-skill.py` 静态断言钉住）。**改哪一份会生效，取决于你改的是哪一份** |
| 挂着 ≠ 生效 | `yuque-knowledge` 仓库有源、容器有挂载，但 `hermes skills list` 里**没有它**——当前没有任何一端加载它 |
| 同名 symlink 再现 | 触发 skill 加载跳过（**静默**）。排障第一步：`ls -ld ~/.hermes/skills/<name>*`，发现与原生撞名的项移入 `~/.hermes/skills/.archive/`，**不要放回** |

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
