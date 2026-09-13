#!/usr/bin/env bash
# =============================================================
# setup-dns.sh — 配置中国域名 DNS 解析
#
# 当系统 DNS 无法正确解析中国域名时（如使用境外 DNS），
# 通过 macOS /etc/resolver/ 按域名路由到可用的国内 DNS，
# 同时在 /etc/hosts 中写入备份条目。
#
# ⚠️ DNS 服务器不写死，按优先级自动选取**第一个探测可用**的：
#     1. DNS_NAMESERVER 环境变量（显式指定）
#     2. 现有 /etc/resolver/ 配置（保持现状，避免来回切换）
#     3. 公共 DNS：223.5.5.5 / 119.29.29.29 / 180.76.76.76
#     4. 本机默认网关（家庭/办公路由器 DNS）
#    全部不可用时**报错退出**，绝不把不可用的 DNS 写进 /etc/resolver/。
#    背景：223.5.5.5 在部分网络（校园网/运营商）被拦 53 端口，硬编码它
#    会让解析彻底失效 —— 2026-08-14 与 2026-09-10 两次事故的成因。
#
# 用法: ./scripts/setup-dns.sh [--dry-run]
#   --dry-run  只演练 DNS 选择过程，不写任何文件、不需要 sudo
# 需要 sudo 权限（写 /etc/resolver/ 和 /etc/hosts）
# =============================================================
set -euo pipefail

# 需要配置 resolver 的域名列表
# ─ 服务主域名 ─
# ─ CDN/GSLB 外域（CNAME 链经过这些域，境外 DNS 无法解析）──
#   alibabadns.com  — 钉钉 api.dingtalk.com CNAME 链
#   eo.dnse1.com    — DeepSeek api.deepseek.com CNAME 链（火山引擎 CDN）
#   eo.dnse5.com    — WorkBuddy workbuddy.cn CNAME 链（腾讯 EdgeOne）
#   bytedns1.com    — 飞书 open.feishu.cn CNAME 链（字节 CDN）
#   aliyunddos1022.com — Moonshot api.moonshot.cn CNAME 链（阿里云 DDoS 防护）
#   yundunwaf3.com  — 智谱 open.bigmodel.cn CNAME 链（阿里云 WAF）
#   cdngslb.com     — 飞书 CDN GSLB 二级跳转
#   gtm-a4b8.com    — 智谱 GTM 跳转
#   queniuyk.com    — 飞书 open.feishu.cn CNAME 终端（金山云 CDN）
#   queniuck.com    — 飞书 msg-frontier.feishu.cn CNAME 终端
#   xiaomimimo.com  — 小米 MiMo api.xiaomimimo.com 主域（mimo-v2.5 多模态模型）
#   xiaomi.com      — 小米 MiMo CNAME 链 (mimo-pri-alisgp.alb.xiaomi.com)
#   workbuddy.cn    — WorkBuddy 主域（www.workbuddy.cn 官网）
RESOLVER_DOMAINS=(
  alibabadns.com
  aliyunddos1022.com
  bigmodel.cn
  bytedns1.com
  cdngslb.com
  deepseek.com
  dingtalk.com
  eo.dnse1.com
  eo.dnse5.com
  feishu.cn
  gitcode.com
  gtm-a4b8.com
  moonshot.cn
  open.bigmodel.cn
  yundunwaf3.com
  zhipu.ai
  # 邮箱
  qq.com
  queniuyk.com
  queniuck.com
  xiaomimimo.com
  xiaomi.com
  workbuddy.cn
)

# /etc/hosts 备份条目（这些域的 IP 可能随 CDN 变化，脚本自动获取最新 IP）
# 注意：只在 DNS 解析确实不稳时才写死 IP；CDN IP 会轮换，写死后必须靠本脚本刷新。
HOSTS_DOMAINS=(
  open.bigmodel.cn
  mcp.dingtalk.com
  wss-open-connection.dingtalk.com
  imap.qq.com
  smtp.qq.com
  api.xiaomimimo.com
  workbuddy.cn
  www.workbuddy.cn
)

# 验证解析用的域名
VERIFY_DOMAINS=(
  api.deepseek.com
  open.bigmodel.cn
  api.dingtalk.com
  wss-open-connection.dingtalk.com
  open.feishu.cn
  api.moonshot.cn
  api.xiaomimimo.com
  workbuddy.cn
)

# 探测 DNS 可用性时使用的域名（要求国内可解析且长期稳定）
PROBE_DOMAINS=(www.qq.com api.deepseek.com)

DRY_RUN=false
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=true ;;
  -h|--help)
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "❌ 未知参数: $1（可用: --dry-run, --help）" >&2
    exit 2
    ;;
esac

# ── 探测某个 DNS 能否解析国内域名（2s 超时，失败静默）──────────
probe_dns() {
  local ns="$1" d
  [[ -z "${ns}" ]] && return 1
  for d in "${PROBE_DOMAINS[@]}"; do
    if dig +short +time=2 +tries=1 "@${ns}" "${d}" A 2>/dev/null \
        | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
      return 0
    fi
  done
  return 1
}

# 现有 /etc/resolver/ 指向的 DNS（取第一个）
existing_resolver_nameserver() {
  local f ns
  for f in /etc/resolver/*; do
    [[ -f "${f}" ]] || continue
    ns="$(awk '/^[[:space:]]*nameserver[[:space:]]/{print $2; exit}' "${f}" 2>/dev/null || true)"
    if [[ -n "${ns}" ]]; then
      echo "${ns}"
      return 0
    fi
  done
  return 1
}

default_gateway() {
  route -n get default 2>/dev/null | awk '/gateway:/{print $2; exit}' || true
}

# 按优先级选出第一个可用的 DNS；成功则打印到 stdout，失败返回 1
select_nameserver() {
  local ns existing gw entry candidate label
  existing="$(existing_resolver_nameserver || true)"
  gw="$(default_gateway)"

  local candidates=()
  [[ -n "${DNS_NAMESERVER:-}" ]] && candidates+=("${DNS_NAMESERVER}|DNS_NAMESERVER 显式指定")
  [[ -n "${existing}" ]] && candidates+=("${existing}|现有 /etc/resolver 配置")
  candidates+=("223.5.5.5|阿里云公共 DNS")
  candidates+=("119.29.29.29|DNSPod 公共 DNS")
  candidates+=("180.76.76.76|百度公共 DNS")
  [[ -n "${gw}" ]] && candidates+=("${gw}|默认网关")

  local seen=" "
  for entry in "${candidates[@]}"; do
    candidate="${entry%%|*}"
    label="${entry##*|}"
    [[ -z "${candidate}" ]] && continue
    [[ "${seen}" == *" ${candidate} "* ]] && continue
    seen+="${candidate} "
    if probe_dns "${candidate}"; then
      echo "   ✅ ${candidate} — 可用（${label}）" >&2
      echo "${candidate}"
      return 0
    fi
    echo "   ⏭️  ${candidate} — 无响应（${label}）" >&2
  done
  return 1
}

# ── 检查是否在 macOS 上运行 ──────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ 此脚本仅支持 macOS（使用 /etc/resolver/ 机制）"
  exit 1
fi

echo "🔧 中国域名 DNS 配置工具"
echo ""
echo "🔍 选择 DNS 服务器..."

NAMESERVER="$(select_nameserver)" || {
  echo "" >&2
  echo "❌ 没有可用的 DNS：全部候选均无响应（53 端口可能被当前网络拦截）" >&2
  echo "   可用 DNS_NAMESERVER=<ip> 显式指定一个可用的 DNS 后重试" >&2
  exit 1
}
echo "   ➡️  选定 DNS: ${NAMESERVER}"
echo ""

if [[ "${DRY_RUN}" == true ]]; then
  echo "✅ --dry-run 演练结束：未写入任何文件（/etc/resolver/ 与 /etc/hosts 均未改动）"
  exit 0
fi

# ── 检查 sudo 权限 ────────────────────────────────────────────
if ! sudo -n true 2>/dev/null; then
  echo "⚠️  需要 sudo 权限来写入 /etc/resolver/ 和 /etc/hosts"
  echo "   请输入密码后继续..."
  sudo -v
fi

# ── 备份现有 /etc/resolver 配置 ──────────────────────────────
if compgen -G "/etc/resolver/*" > /dev/null; then
  backup="${HOME}/resolver-backup-$(date +%Y%m%d-%H%M%S).tgz"
  sudo tar -czf "${backup}" -C /etc resolver 2>/dev/null || true
  if [[ -f "${backup}" ]]; then
    sudo chown "$(id -u):$(id -g)" "${backup}" 2>/dev/null || true
    echo "💾 已备份现有配置 → ${backup}"
  fi
fi

# ── 配置 /etc/resolver/ ──────────────────────────────────────
echo ""
echo "📋 配置 /etc/resolver/ ..."
sudo mkdir -p /etc/resolver

created=0
skipped=0
for domain in "${RESOLVER_DOMAINS[@]}"; do
  target="/etc/resolver/${domain}"
  expected="nameserver ${NAMESERVER}"

  if [[ -f "${target}" ]]; then
    current=$(cat "${target}" 2>/dev/null)
    if [[ "${current}" == "${expected}" ]]; then
      echo "   ✅ ${domain} — 已存在，跳过"
      skipped=$((skipped + 1))
      continue
    else
      echo "   🔄 ${domain} — 更新配置"
    fi
  else
    echo "   🆕 ${domain} — 创建配置"
  fi

  echo "${expected}" | sudo tee "${target}" > /dev/null
  created=$((created + 1))
done

echo "   新建/更新: ${created}，跳过: ${skipped}"

# ── 刷新 DNS 缓存 ────────────────────────────────────────────
echo ""
echo "🔄 刷新 DNS 缓存..."
sudo dscacheutil -flushcache 2>/dev/null || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

# ── 更新 /etc/hosts 备份条目 ─────────────────────────────────
echo ""
echo "📋 更新 /etc/hosts 备份条目..."

for domain in "${HOSTS_DOMAINS[@]}"; do
  # 通过刚配置的 resolver 获取最新 IP
  ip=$(dig @${NAMESERVER} +short "${domain}" A 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | tail -1)

  if [[ -z "${ip}" ]]; then
    echo "   ⚠️  ${domain} — 无法解析，跳过"
    continue
  fi

  # 检查 /etc/hosts 中是否已有该域名的条目
  existing_ip=$(grep -w "${domain}" /etc/hosts 2>/dev/null | awk '{print $1}' | head -1)

  if [[ -n "${existing_ip}" && "${existing_ip}" == "${ip}" ]]; then
    echo "   ✅ ${domain} → ${ip} — 已是最新，跳过"
  elif [[ -n "${existing_ip}" ]]; then
    echo "   🔄 ${domain} → ${ip}（旧: ${existing_ip}）"
    sudo sed -i.bak "s/${existing_ip}[[:space:]]\\+${domain}/${ip}	${domain}/" /etc/hosts
    sudo rm -f /etc/hosts.bak
  else
    echo "   🆕 ${domain} → ${ip}"
    echo "${ip}	${domain}" | sudo tee -a /etc/hosts > /dev/null
  fi
done

# ── 验证解析 ─────────────────────────────────────────────────
echo ""
echo "🧪 验证 DNS 解析..."

FAIL=0
for domain in "${VERIFY_DOMAINS[@]}"; do
  if python3 -c "import socket; socket.getaddrinfo('${domain}', 443)" 2>/dev/null; then
    ip=$(python3 -c "import socket; print(socket.getaddrinfo('${domain}', 443)[0][4][0])" 2>/dev/null)
    echo "   ✅ ${domain} → ${ip}"
  else
    echo "   ❌ ${domain} — 解析失败"
    FAIL=$((FAIL + 1))
  fi
done

echo ""
if [[ ${FAIL} -eq 0 ]]; then
  echo "✅ 全部解析成功（DNS: ${NAMESERVER}）"
else
  echo "⚠️  ${FAIL} 个域名解析失败，请检查网络连接"
  echo "   提示：如果使用代理工具，请确保中国 IP 走直连"
  exit 1
fi
