#!/usr/bin/env bash
# =============================================================
# check-cloud-root.sh — 校验备份根目录确实经过配置
#
# 为什么需要这个守卫
# ------------------
# docker-compose.yml 里写的是：
#     - ${BACKUP_ROOT:-/tmp/myopenclaw-backups}:/backup:rw
# BACKUP_ROOT 是**宿主机环境变量**，由 scripts/start.sh 解析 .cloud.conf 后
# export，再传给 docker compose。不带它直接跑
# `docker compose up -d backup-cron`，compose 会静默回退到
# /tmp/myopenclaw-backups —— 而 macOS 的 periodic 每天会清理 /tmp 中 3 天以上
# 未访问的文件。
#
# 危险之处在于**完全静默**：容器内部永远只看到 /backup，BACKUP_ROOT 永远非空，
# crontab 照常触发、脚本退出码 0、日志「✅ 全部备份完成」、容器 healthy ——
# 数据却写进了一个会被系统清掉的目录。2026-09-13 实测踩中。
#
# 哨兵文件由 scripts/start.sh 在配置备份根目录时写入，内容为解析出的宿主机
# 绝对路径。它校验的是「这个目录经过配置」，**与是否云盘无关** ——
# 本地目录（.cloud.conf 的 custom provider）同样合法。
#
# 用法: check-cloud-root.sh [备份根目录]    # 默认 /backup
# 退出码: 0 已配置；1 未配置（调用方应拒绝启动）
# =============================================================
set -euo pipefail

ROOT="${1:-/backup}"
SENTINEL="${ROOT}/.myopenclaw-backup-root"

if [[ -f "${SENTINEL}" ]]; then
  host_path="$(head -1 "${SENTINEL}")"
  echo "☁️  备份根目录已配置: ${ROOT} → ${host_path}"
  exit 0
fi

cat >&2 <<EOF
❌ 拒绝启动：${ROOT} 不是经配置的备份目录
   缺少标记文件 ${SENTINEL}

   这通常意味着容器是用默认值启动的 —— docker-compose.yml 在拿不到
   BACKUP_ROOT 时会回退到 /tmp/myopenclaw-backups，而 macOS 每天清理
   /tmp 里 3 天以上未访问的文件：备份会被系统悄悄删掉、也永远不会同步到
   任何地方，且过程中没有任何报错。

   正确做法（二选一）：

     1. 用 start.sh 启动（会读 .cloud.conf 并标记备份目录）
          ./scripts/start.sh

     2. 显式指定备份目录
          BACKUP_ROOT=/path/to/backup docker compose up -d backup-cron

   若尚未配置云盘/备份位置，先创建 .cloud.conf：
          cp .cloud.conf.example .cloud.conf   # 然后按需修改
EOF
exit 1
