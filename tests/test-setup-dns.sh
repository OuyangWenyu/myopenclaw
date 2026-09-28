#!/usr/bin/env bash
# Test: setup-dns.sh 的 DNS 选择逻辑
#
# 全部走 --dry-run（不写文件、不需要 sudo）。
# 回归背景：脚本曾硬编码 NAMESERVER=223.5.5.5，而该 DNS 在校园网/部分运营商
# 被拦 53 端口 —— 谁跑一次就把 /etc/resolver/ 写成不可用配置，解析全挂。
#
# 用法: bash tests/test-setup-dns.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/setup-dns.sh"
PASS=0
FAIL=0

check() {
    local desc="$1"
    if eval "$2"; then
        echo "  ✓ $desc"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

# /etc/resolver 内容指纹（用于确认 dry-run 没有写入）
resolver_fingerprint() {
    find /etc/resolver -type f -exec cat {} + 2>/dev/null | shasum | awk '{print $1}'
}

echo "=== Test: setup-dns.sh DNS 选择逻辑 ==="
echo "脚本: $SCRIPT"
echo ""

# 0. 语法可解析
check "bash -n 语法检查通过" \
    "bash -n '${SCRIPT}'"

# 1. --dry-run 成功选出一个合法 DNS
out="$(bash "${SCRIPT}" --dry-run 2>&1)"
rc=$?
check "--dry-run 退出码为 0" \
    "[[ ${rc} -eq 0 ]]"
check "--dry-run 打印了合法 IPv4 的选定 DNS" \
    "echo \"\${out}\" | grep -qE '选定 DNS: ([0-9]{1,3}\\.){3}[0-9]{1,3}'"
check "--dry-run 明确标注未写入文件" \
    "echo \"\${out}\" | grep -q '未写入'"

# 2. --dry-run 不修改 /etc/resolver
before="$(resolver_fingerprint)"
bash "${SCRIPT}" --dry-run > /dev/null 2>&1
after="$(resolver_fingerprint)"
check "--dry-run 未修改 /etc/resolver" \
    "[[ '${before}' == '${after}' ]]"

# 3. 显式指定一个不可用的 DNS（TEST-NET-3，保留地址必然无响应）
#    不得把它当成选定值 —— 要么回退到其它可用 DNS，要么报错退出
out="$(DNS_NAMESERVER=203.0.113.9 bash "${SCRIPT}" --dry-run 2>&1)"
rc=$?
check "不可用的显式 DNS 不会被选定" \
    "! echo \"\${out}\" | grep -q '选定 DNS: 203.0.113.9'"
check "不可用时要么回退成功(0) 要么明确失败(非 0)" \
    "[[ ${rc} -eq 0 ]] || echo \"\${out}\" | grep -qE '没有可用 DNS|不可用'"

# 4. 未知参数要拒绝，且不得触发任何写入
out="$(bash "${SCRIPT}" --bogus 2>&1)"
rc=$?
check "未知参数退出码为 2" \
    "[[ ${rc} -eq 2 ]]"

# 5. 关键域名的 CNAME 终端域必须都在 RESOLVER_DOMAINS 清单里
#    背景（2026-09-28 飞书登录页事故）：CNAME 链末端的外域（queniurc.com / queniuiq.com）
#    不在清单里时，解析**不会失败** —— 它静默回落到默认 DNS（境外 VPN 场景是 1.1.1.3），
#    拿到海外 CDN 节点，流量被 VPN 隧道吸走。所以必须逐链核对终端域。
#    DNS 无应答（离线/被拦）时跳过该域名，不算失败 —— --dry-run 那部分已覆盖 DNS 选择逻辑。
echo ""
echo "--- CNAME 终端域是否被 RESOLVER_DOMAINS 覆盖 ---"

resolver_list="$(awk '/^RESOLVER_DOMAINS=\(/,/^\)/' "${SCRIPT}" | sed -n 's/^  \([a-z0-9.-]*\)$/\1/p')"
selected_ns="$(bash "${SCRIPT}" --dry-run 2>/dev/null | sed -n 's/.*选定 DNS: \([0-9.][0-9.]*\).*/\1/p' | head -1)"

if [[ -z "${selected_ns}" ]]; then
    check "能从 --dry-run 取到可用 DNS（CNAME 终端检查的前提）" "false"
else
    for host in accounts.feishu.cn www.feishu.cn help.feishu.cn open.feishu.cn drive.feishu.cn; do
        chain="$(dig +short +time=3 "${host}" "@${selected_ns}" 2>/dev/null || true)"
        if [[ -z "${chain}" ]]; then
            echo "  - 跳过 ${host}（${selected_ns} 无应答，不计失败）"
            continue
        fi
        missing=""
        # 取链上每个 FQDN，归约到末两段（本机所有 CNAME 链外域都是 .com 单后缀）
        for tok in $(printf '%s\n' "${chain}" | grep -oE '[A-Za-z0-9._-]+\.[A-Za-z]{2,}'); do
            case "${tok}" in
                *[!0-9.]*)
                    base="$(printf '%s' "${tok}" | awk -F. '{print $(NF-1)"."$NF}')"
                    printf '%s\n' "${resolver_list}" | grep -qx "${base}" || missing="${missing} ${base}"
                    ;;
            esac
        done
        missing="$(printf '%s\n' ${missing} | sort -u | tr '\n' ' ' | sed 's/ *$//')"
        if [[ -n "${missing}" ]]; then
            check "${host} 的 CNAME 终端域在 RESOLVER_DOMAINS 里（缺: ${missing}）" "false"
        else
            check "${host} 的 CNAME 终端域在 RESOLVER_DOMAINS 里" "true"
        fi
    done
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: setup-dns.sh DNS 选择逻辑健壮 ==="
