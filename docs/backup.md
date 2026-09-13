# 备份系统

backup-cron 容器每天凌晨 2:00 对所有持久化数据做快照备份到云盘（`BACKUP_CRON` 可改）。频率与 AgentOps 备份过期阈值（24h）对齐。

## ⚠️ 备份根目录与守卫

备份往哪写由**宿主机环境变量 `BACKUP_ROOT`** 决定，而它是 `scripts/start.sh` 解析 `.cloud.conf` 后 `export` 的。`docker-compose.yml` 里写的是：

```yaml
- ${BACKUP_ROOT:-/tmp/myopenclaw-backups}:/backup:rw
```

也就是说，**不经 `start.sh` 直接跑 `docker compose up -d backup-cron`，compose 会静默回退到 `/tmp/myopenclaw-backups`** —— 而 macOS 每天清理 `/tmp` 里 3 天以上未访问的文件，备份会被系统删掉且毫无报错。（2026-09-13 实测踩中。）

因此容器启动时会校验备份根目录里的标记文件 `.myopenclaw-backup-root`（由 `start.sh` 写入，内容为解析出的宿主机路径）：

- **有标记** → 打印 `☁️ 备份根目录已配置: /backup → <宿主机路径>` 后正常启动
- **无标记** → **拒绝启动并退出**，日志给出修复命令

标记校验的是「该目录经过配置」，**与是否云盘无关** —— 本地目录（`.cloud.conf` 的 `CLOUD_PROVIDER=custom`）同样合法。

**容器重启不会删除快照**：启动时的初始备份带 `BACKUP_SKIP_PRUNE=1`，保留策略只由 02:00 的定时任务执行，重启这一运维动作不携带删除副作用。

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
| Hermes | `config.yaml`、`SOUL.md`、`memories/`、`skills/`、`hooks/`、`cron/`、`.contacts/`、`.config/himalaya/`、`.config/ortie/`（Outlook OAuth token） |
| Claude Code | `settings.json`、`projects/`、`skills/`、`plans/`、`tasks/`、cc-connect `config.toml` |
| OpenClaw | `openclaw.json`、`agents/`、`flows/`、`extensions/`、`memory/main.sqlite`（热备份）、`memory-tdai/memories.sqlite`（虾酱记忆） |
| TDAI Memory | `memories.sqlite`（sqlite3 热备）、`scene_blocks/`、`persona.md`、`checkpoint.json` |
| Data | `~/.myagentdata/` 整目录 rsync |

## 不备份的内容

- 大型缓存、临时会话、日志
- `~/.config/gh`、`~/.config/opencode` 中的敏感内容（需重新配置）
- `~/.hermes/secrets/`（API Key 文件）
- 其他 auth token（Outlook 的 ortie token 例外：恢复后无需重新浏览器授权）

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
BACKUP_CRON="0 2 * * *"    # cron 表达式（默认每天凌晨 2:00）
BACKUP_KEEP_DAYS=30        # 快照保留天数
```

`.cloud.conf` 中的 `BACKUP_ROOT` 指定云盘路径（Google Drive / OneDrive / 自定义）。

已有部署若 `.env` 仍是 `BACKUP_CRON=0 2 * * 0`（每周日），改完需要重建容器才会换上新 crontab：

```bash
./scripts/start.sh                     # 推荐：会一并导出 BACKUP_ROOT
```

> ⚠️ **不要**用裸的 `docker compose up -d backup-cron` —— 它拿不到 `BACKUP_ROOT`，
> 会静默回退到 `/tmp/myopenclaw-backups`（见文首「备份根目录与守卫」）。容器现在
> 会拒绝在这种状态下启动，但直接用 `start.sh` 更省事。
