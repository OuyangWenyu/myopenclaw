#!/usr/bin/env bash
# Load EMAIL*/EMAIL2*/EMAIL_OUTLOOK* vars from a Hermes .env file into the
# CALLING shell as SHELL-LOCAL variables (never exported).
#
# 设计要点（历史教训，勿回退）：
#   1. 变量名前缀匹配 `^EMAIL`（无下划线）——`^EMAIL_` 匹配不到 EMAIL2_*，
#      曾导致 dlut 账户配置无法从 .env 重新生成；
#   2. 注释行同样生效：先剥行首 `#` 再解析——QQ/DLUT 凭据在 .env 里保持
#      注释状态（防止 Hermes 把 email 当消息平台），但配置生成要读到它们；
#   3. 赋值用 `printf -v`，不用 eval——值允许空格（"Wenyu Ouyang" 曾被
#      eval 拆成赋值+执行命令），$(...) 作为字面量存储、绝不执行；
#   4. 容器环境（compose 注入的 EMAIL_OUTLOOK_*）优先：已存在的变量不覆盖；
#   5. 变量只赋值不导出——邮箱密码绝不进入 Hermes 进程环境
#      （wrapper 用 `source` 本脚本后调用 load_email_env）。
#
# 直接执行（带文件参数）时自测：仅打印已加载的变量名，绝不打印值。
load_email_env() {
  local env_file="${1:?env file path required}"
  [[ -f "${env_file}" ]] || return 0
  local kv key value
  while IFS= read -r kv || [[ -n "${kv}" ]]; do
    kv="${kv%$'\r'}"
    # 剥行首空白 + 一个前导 '#' + 再剥空白（注释行同样生效）
    kv="${kv#"${kv%%[![:space:]]*}"}"
    if [[ "${kv}" == \#* ]]; then
      kv="${kv#\#}"
      kv="${kv#"${kv%%[![:space:]]*}"}"
    fi
    [[ "${kv}" == *=* ]] || continue
    key="${kv%%=*}"
    value="${kv#*=}"
    # 只接受合法的 EMAIL 系变量名（防非法标识符流入 printf -v）
    [[ "${key}" =~ ^EMAIL[A-Za-z0-9_]*$ ]] || continue
    if [[ -z "${!key:-}" ]]; then
      printf -v "${key}" '%s' "${value}"
    fi
  done < "${env_file}"
}

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  return 0 2>/dev/null || true  # sourced — nothing else to do
fi

# 自测入口：bash load-email-env.sh <env-file> → 只打印变量名
if [[ -n "${1:-}" ]]; then
  load_email_env "${1}"
  compgen -A variable | grep -E '^EMAIL' | sort || true
fi
