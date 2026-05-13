#!/usr/bin/env bash
# =============================================================
# setup-dns.sh — 配置中国域名 DNS 解析
#
# 当系统 DNS 无法正确解析中国域名时（如使用境外 DNS），
# 通过 macOS /etc/resolver/ 按域名路由到国内 DNS (223.5.5.5)，
# 同时在 /etc/hosts 中写入备份条目。
#
# 用法: ./scripts/setup-dns.sh
# 需要 sudo 权限（写 /etc/resolver/ 和 /etc/hosts）
# =============================================================
set -euo pipefail

NAMESERVER="223.5.5.5"

# 需要配置 resolver 的域名列表
# alibabadns.com — 钉钉 CNAME 链经过此域（关键！）
# 其余为各服务主域名
RESOLVER_DOMAINS=(
  alibabadns.com
  bigmodel.cn
  deepseek.com
  dingtalk.com
  feishu.cn
  gitcode.com
  moonshot.cn
  open.bigmodel.cn
  zhipu.ai
)

# /etc/hosts 备份条目（这些域的 IP 可能随 CDN 变化，脚本自动获取最新 IP）
HOSTS_DOMAINS=(
  open.bigmodel.cn
  mcp.dingtalk.com
  wss-open-connection.dingtalk.com
)

echo "🔧 中国域名 DNS 配置工具"
echo "   DNS 服务器: ${NAMESERVER} (阿里云公共 DNS)"
echo ""

# ── 检查是否在 macOS 上运行 ──────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ 此脚本仅支持 macOS（使用 /etc/resolver/ 机制）"
  exit 1
fi

# ── 检查 sudo 权限 ────────────────────────────────────────────
if ! sudo -n true 2>/dev/null; then
  echo "⚠️  需要 sudo 权限来写入 /etc/resolver/ 和 /etc/hosts"
  echo "   请输入密码后继续..."
  sudo -v
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
      ((skipped++))
      continue
    else
      echo "   🔄 ${domain} — 更新配置"
    fi
  else
    echo "   🆕 ${domain} — 创建配置"
  fi

  echo "${expected}" | sudo tee "${target}" > /dev/null
  ((created++))
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
    sudo sed -i '' "s/${existing_ip}[[:space:]]\\+${domain}/${ip}	${domain}/" /etc/hosts
  else
    echo "   🆕 ${domain} → ${ip}"
    echo "${ip}	${domain}" | sudo tee -a /etc/hosts > /dev/null
  fi
done

# ── 验证解析 ─────────────────────────────────────────────────
echo ""
echo "🧪 验证 DNS 解析..."

FAIL=0
for domain in api.deepseek.com open.bigmodel.cn api.dingtalk.com wss-open-connection.dingtalk.com open.feishu.cn api.moonshot.cn; do
  if python3 -c "import socket; socket.getaddrinfo('${domain}', 443)" 2>/dev/null; then
    ip=$(python3 -c "import socket; print(socket.getaddrinfo('${domain}', 443)[0][4][0])" 2>/dev/null)
    echo "   ✅ ${domain} → ${ip}"
  else
    echo "   ❌ ${domain} — 解析失败"
    ((FAIL++))
  fi
done

echo ""
if [[ ${FAIL} -eq 0 ]]; then
  echo "✅ 全部解析成功"
else
  echo "⚠️  ${FAIL} 个域名解析失败，请检查网络连接"
  echo "   提示：如果使用代理工具，请确保中国 IP 走直连"
  exit 1
fi
