#!/usr/bin/env bash
# =============================================================
# scripts/lib/backup-prune.sh — 旧快照删除的共享实现
# 由 5 个备份脚本 source（hermes / openclaw / claude / data / tdai-memory）
#
# 为什么需要重试：备份根目录在 macOS 上是 virtiofs → Google Drive File Stream。
# 批量 unlink 之后文件系统会短暂继续报目录非空 —— busybox `rm -rf` 删掉一批
# 子项后以 "can't remove '...': Directory not empty" 退出（不会逐文件报错）。
# 2026-10-26 实测：一次 rm 把 701 项删到 324 项后失败，紧接着再跑一次即删净。
# 旧实现是裸 `rm -rf` + `set -e` ⇒ 每次 prune 最多删掉一批就把整轮备份判为
# 失败：2026-10-16 ~ 10-26 连续 11 天 openclaw「备份失败」（心跳 failed），
# 实际上快照与 latest/ 每次都完整，只有 7 个过期快照没删掉。
#
# 注意：失败时**不要**用「重新扫一遍目录逐项删」的写法 —— 问题不在单个文件，
# 而在目录项删除的可见性滞后；隔一会儿整目录重试才是对治法。
#
# 用法：
#   prune_old_snapshot <dir>
# 返回 0：已删净（或本来就不存在 —— rm 的非零可能只是尾部 ENOTEMPTY 的噪音）
# 返回 1：重试耗尽仍存在，由调用方决定是否把整轮备份判失败
#
#   sync_latest_retry <snapshot_dir> <latest_dir>
#   把快照同步到 latest/ 时的同类问题：rsync --delete 删不掉目标里多余的目录时
#   只打印 "cannot delete non-empty directory" 且退出码仍是 0 —— 于是每天的
#   latest/ 同步都会重演同一行警告，留下空目录（2026-10-26 实测 data 的
#   tdai-memory/.backup 下就留了一个 0 字节空目录）。rsync 幂等，隔一会儿重跑
#   一次即可删净。真错误（非该警告）照常返回非 0，由 set -e 终止。
#   重试耗尽仍有该警告：打印 ⚠️ 并返回 0 —— latest/ 只是便捷镜像，带时间戳的
#   快照才是权威副本，不值得把它判成备份失败。
# =============================================================

PRUNE_RETRY_ATTEMPTS="${PRUNE_RETRY_ATTEMPTS:-5}"
PRUNE_RETRY_SLEEP="${PRUNE_RETRY_SLEEP:-2}"

prune_old_snapshot() {
    local target="$1" attempt out=""
    for ((attempt = 1; attempt <= PRUNE_RETRY_ATTEMPTS; attempt++)); do
        out="$(rm -rf "${target}" 2>&1)" || true
        [[ -e "${target}" ]] || return 0
        if [[ ${attempt} -lt ${PRUNE_RETRY_ATTEMPTS} ]]; then
            sleep "${PRUNE_RETRY_SLEEP}"
        fi
    done
    echo "   ⚠️  删除失败（重试 ${PRUNE_RETRY_ATTEMPTS} 次后仍存在）: ${target}" >&2
    if [[ -n "${out}" ]]; then
        echo "      ${out}" >&2
    fi
    return 1
}

sync_latest_retry() {
    local dest="$1" latest="$2" attempt rc out
    for ((attempt = 1; attempt <= PRUNE_RETRY_ATTEMPTS; attempt++)); do
        if out="$(rsync -a --delete "${dest}/" "${latest}/" 2>&1)"; then
            rc=0
        else
            rc=$?
        fi
        if [[ ${rc} -ne 0 ]]; then
            printf '%s\n' "${out}" >&2
            return "${rc}"
        fi
        if [[ "${out}" != *"cannot delete non-empty directory"* ]]; then
            return 0
        fi
        if [[ ${attempt} -lt ${PRUNE_RETRY_ATTEMPTS} ]]; then
            sleep "${PRUNE_RETRY_SLEEP}"
        fi
    done
    echo "   ⚠️  latest/ 同步有删除残留（重试 ${PRUNE_RETRY_ATTEMPTS} 次）：latest/ 可能多出旧文件，真实快照不受影响" >&2
    if [[ -n "${out}" ]]; then
        echo "      ${out}" >&2
    fi
    return 0
}
