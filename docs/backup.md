# 备份系统

backup-cron 容器定时对所有持久化数据做快照备份到云盘。

## 备份管线

```
backup-all-docker.sh
  ├── hermes/scripts/backup.sh     → Hermes 数据
  ├── openclaw/scripts/backup.sh   → OpenClaw 数据
  ├── claude/scripts/backup.sh     → Claude Code + cc-connect 数据
  ├── scripts/backup-data.sh       → ~/.myagentdata
  └── tdai-memory/scripts/backup.sh → TDAI Memory 数据
```

每个脚本做选择性 rsync 到时间戳快照目录，维护 `latest/` 软链接。失败跟踪：单步失败不中断，最终汇总退出码。

## 备份内容

| 范围 | 内容 |
|------|------|
| Hermes | `config.yaml`、`SOUL.md`、`memories/`、`skills/`、`hooks/`、`cron/` |
| Claude Code | `settings.json`、`projects/`、`skills/`、`plans/`、`tasks/`、cc-connect `config.toml` |
| OpenClaw | `openclaw.json`、`agents/`、`flows/`、`extensions/`、`memory/main.sqlite`（热备份）、`memory-tdai/memories.sqlite`（虾酱记忆） |
| TDAI Memory | `memories.sqlite`（sqlite3 热备）、`scene_blocks/`、`persona.md`、`checkpoint.json` |
| Data | `~/.myagentdata/` 整目录 rsync |

## 不备份的内容

- 大型缓存、临时会话、auth token、日志
- `~/.config/gh`、`~/.config/opencode` 中的敏感内容（需重新配置）
- `~/.hermes/secrets/`（API Key 文件）

## 手动备份

```bash
docker compose exec backup-cron /scripts/backup-all-docker.sh
```

## 快照管理

快照保存在：`<云盘路径>/myopenclaw-backups/<类别>/<时间戳>/`

每个快照为独立时间戳目录，`latest/` 软链接指向最新。超过 `BACKUP_KEEP_DAYS`（默认 30 天）的旧快照自动清除。

## 恢复

```bash
./scripts/restore.sh all latest            # 恢复全部最新快照
./scripts/restore.sh hermes latest         # 恢复单个
./scripts/restore.sh claude 2026-04-23_090000  # 指定时间戳
```

> 如果恢复了 `~/.openclaw/openclaw.json` 或 `~/.cc-connect/config.toml`，start.sh 不会覆盖它们（只在文件不存在时从模板创建）。

## 配置

在 `.env` 中调整：

```bash
BACKUP_CRON="0 2 * * 0"    # cron 表达式（默认每周日凌晨 2:00）
BACKUP_KEEP_DAYS=30        # 快照保留天数
```

`.cloud.conf` 中的 `BACKUP_ROOT` 指定云盘路径（Google Drive / OneDrive / 自定义）。
