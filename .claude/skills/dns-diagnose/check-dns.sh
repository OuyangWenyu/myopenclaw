#!/usr/bin/env bash
# check-dns.sh — 中国服务 DNS 诊断工具
# 检查所有 /etc/resolver/ 配置和容器内 DNS 解析状态
# 用法: bash .claude/skills/dns-diagnose/check-dns.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { echo -e "   ${GREEN}✅${NC} $1"; }
fail() { echo -e "   ${RED}❌${NC} $1"; }
warn() { echo -e "   ${YELLOW}⚠️${NC}  $1"; }

echo "🔧 中国服务 DNS 诊断"
echo "===================="
echo ""

# ── 1. 检查 /etc/resolver/ 文件 ──────────────────────────
echo "1️⃣  /etc/resolver/ 配置检查"
echo ""

# 期望的 resolver 域名列表（与 scripts/setup-dns.sh 同步）
EXPECTED_DOMAINS=(
  alibabadns.com aliyunddos1022.com bigmodel.cn bytedns1.com
  cdngslb.com deepseek.com dingtalk.com eo.dnse1.com
  feishu.cn gitcode.com gtm-a4b8.com moonshot.cn
  open.bigmodel.cn qq.com queniuyk.com queniuck.com
  yundunwaf3.com zhipu.ai
)

MISSING_RESOLVERS=()
for domain in "${EXPECTED_DOMAINS[@]}"; do
  target="/etc/resolver/${domain}"
  if [[ -f "${target}" ]]; then
    content=$(cat "${target}" 2>/dev/null)
    if [[ "${content}" == "nameserver 223.5.5.5" ]]; then
      pass "${domain}"
    else
      warn "${domain} — 内容异常: ${content}"
    fi
  else
    fail "${domain} — 文件缺失"
    MISSING_RESOLVERS+=("${domain}")
  fi
done

echo ""
if [[ ${#MISSING_RESOLVERS[@]} -gt 0 ]]; then
  echo -e "   ${RED}缺失 ${#MISSING_RESOLVERS[@]} 个 resolver 文件${NC}"
  echo ""
  echo "   修复命令:"
  for domain in "${MISSING_RESOLVERS[@]}"; do
    echo "     echo 'nameserver 223.5.5.5' | sudo tee /etc/resolver/${domain}"
  done
  echo "     sudo dscacheutil -flushcache"
  echo "     sudo killall -HUP mDNSResponder"
else
  echo "   ${GREEN}全部 resolver 文件就绪${NC}"
fi

echo ""

# ── 2. 主机 DNS 解析测试 ────────────────────────────────
echo "2️⃣  主机 DNS 解析测试"
echo ""

SERVICE_DOMAINS=(
  "api.dingtalk.com:钉钉 API"
  "wss-open-connection.dingtalk.com:钉钉 WebSocket"
  "open.feishu.cn:飞书 Open API"
  "msg-frontier.feishu.cn:飞书消息网关"
  "open.bigmodel.cn:智谱 API"
  "api.moonshot.cn:Moonshot API"
  "api.deepseek.com:DeepSeek API"
  "api.zhipu.ai:智谱 AI"
  "imap.qq.com:QQ 邮箱 IMAP"
  "smtp.qq.com:QQ 邮箱 SMTP"
  "gitcode.com:GitCode"
)

HOST_FAILURES=()
for entry in "${SERVICE_DOMAINS[@]}"; do
  domain="${entry%%:*}"
  label="${entry##*:}"
  if python3 -c "import socket; socket.getaddrinfo('${domain}', 443)" 2>/dev/null; then
    ip=$(python3 -c "import socket; print(socket.getaddrinfo('${domain}', 443)[0][4][0])" 2>/dev/null)
    pass "${label} (${domain}) → ${ip}"
  else
    fail "${label} (${domain})"
    HOST_FAILURES+=("${domain}")
  fi
done

echo ""

# ── 3. 容器内 DNS 解析测试 ──────────────────────────────
echo "3️⃣  容器内 DNS 解析测试"
echo ""

# 检查 Docker 是否运行
if ! docker info &>/dev/null 2>&1; then
  warn "Docker 未运行，跳过容器内测试"
else
  # 检查 hermes 容器是否运行
  RUNNING=$(docker compose -f "${REPO_ROOT}/docker-compose.yml" ps hermes --format json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('State',''))" 2>/dev/null || echo "")

  if [[ "${RUNNING}" != "running" ]]; then
    warn "hermes 容器未运行，跳过容器内测试"
  else
    CONTAINER_FAILURES=()
    for entry in "${SERVICE_DOMAINS[@]}"; do
      domain="${entry%%:*}"
      label="${entry##*:}"
      result=$(docker compose -f "${REPO_ROOT}/docker-compose.yml" exec -T hermes python3 -c "import socket; socket.getaddrinfo('${domain}', 443)" 2>&1)
      if echo "$result" | grep -q "Errno\|error"; then
        fail "${label} (${domain}) — 容器内无法解析"
        CONTAINER_FAILURES+=("${domain}")
      else
        pass "${label} (${domain})"
      fi
    done
    echo ""

    # ── 4. 诊断容器内失败原因 ───────────────────────────
    if [[ ${#CONTAINER_FAILURES[@]} -gt 0 ]]; then
      echo "4️⃣  容器内 DNS 失败诊断"
      echo ""

      for domain in "${CONTAINER_FAILURES[@]}"; do
        echo "   ── ${domain} ──"

        # 检查是否主机能解析但容器不能（说明是 Docker DNS 问题）
        host_ok=true
        python3 -c "import socket; socket.getaddrinfo('${domain}', 443)" 2>/dev/null || host_ok=false

        if ${host_ok}; then
          # 主机通容器不通 → 检查 CNAME 链
          echo "   主机可解析，容器不可 — 检查 CNAME 链..."

          # 用 dig 追踪 CNAME 链，找出不在 resolver 列表中的中间域名
          cname_chain=$(dig @223.5.5.5 "${domain}" +trace 2>/dev/null | grep -E 'CNAME|IN\s+A' | head -20 || true)
          if [[ -z "${cname_chain}" ]]; then
            # 直接查询
            cname_chain=$(dig @223.5.5.5 "${domain}" 2>/dev/null | grep -E 'CNAME|IN\s+A' | grep -v '^;' | head -20 || true)
          fi

          # 提取 CNAME 中的域名
          cname_domains=$(echo "${cname_chain}" | grep 'CNAME' | awk '{print $NF}' | sed 's/\.$//')

          missing_resolver_for_cname=()
          for cname in ${cname_domains}; do
            # 提取顶级域名部分
            tld=$(echo "${cname}" | awk -F'.' '{n=NF; if(n>2) print $(n-1)"."$n; else print $0}')
            # 尝试更长的后缀匹配
            for suffix in $(echo "${cname}" | tr '.' '\n' | tail -r | head -3 | tail -r | paste -sd '.' -); do
              :
            done

            # 简单检查: 取最后两段作为域名，检查是否有 resolver
            base_domain=$(echo "${cname}" | grep -oE '[a-z0-9-]+\.[a-z]+$' || echo "")
            if [[ -n "${base_domain}" ]] && [[ ! -f "/etc/resolver/${base_domain}" ]]; then
              missing_resolver_for_cname+=("${base_domain}")
            fi
          done

          if [[ ${#missing_resolver_for_cname[@]} -gt 0 ]]; then
            # 去重
            unique_missing=($(printf '%s\n' "${missing_resolver_for_cname[@]}" | sort -u))
            echo "   ${RED}缺失 resolver 文件:${NC}"
            for m in "${unique_missing[@]}"; do
              echo "     • ${m}"
              echo "       修复: echo 'nameserver 223.5.5.5' | sudo tee /etc/resolver/${m}"
            done
          fi

          # 显示完整 CNAME 链
          echo ""
          echo "   CNAME 链路:"
          dig @223.5.5.5 "${domain}" 2>/dev/null | grep -E 'CNAME|IN\s+A' | grep -v '^;' | while read -r line; do
            if echo "${line}" | grep -q 'CNAME'; then
              target=$(echo "${line}" | awk '{print $NF}' | sed 's/\.$//')
              has_resolver=""
              base=$(echo "${target}" | grep -oE '[a-z0-9-]+\.[a-z]+$' || echo "")
              if [[ -n "${base}" ]] && [[ -f "/etc/resolver/${base}" ]]; then
                has_resolver=" ✅"
              elif [[ -n "${base}" ]]; then
                has_resolver=" ❌ 无 resolver"
              fi
              echo "     ↳ CNAME → ${target}${has_resolver}"
            elif echo "${line}" | grep -q 'IN\s+A'; then
              ip=$(echo "${line}" | awk '{print $NF}')
              echo "     📍 A → ${ip}"
            fi
          done
        else
          echo "   ${RED}主机也无法解析 — 检查网络连接和 Astrill VPN${NC}"
          echo "   - 确认 223.5.5.5 可达: ping -c 2 223.5.5.5"
          echo "   - 确认 Astrill 未劫持 DNS"
        fi
        echo ""
      done
    fi
  fi
fi

# ── 5. 总结 ───────────────────────────────────────────
echo "5️⃣  总结"
echo ""
HOST_TOTAL=$((${#SERVICE_DOMAINS[@]} - ${#HOST_FAILURES[@]}))
echo "   主机解析: ${HOST_TOTAL}/${#SERVICE_DOMAINS[@]}"

if [[ -n "${CONTAINER_FAILURES:-}" ]]; then
  CONTAINER_TOTAL=$((${#SERVICE_DOMAINS[@]} - ${#CONTAINER_FAILURES[@]}))
  echo "   容器解析: ${CONTAINER_TOTAL}/${#SERVICE_DOMAINS[@]}"
fi

if [[ ${#MISSING_RESOLVERS[@]} -gt 0 ]] || [[ ${#HOST_FAILURES[@]} -gt 0 ]] || [[ ${#CONTAINER_FAILURES[@]:-0} -gt 0 ]]; then
  echo ""
  echo -e "   ${RED}发现问题，需要修复${NC}"
  echo ""
  echo "   快速修复（添加缺失 resolver + 刷新 DNS + 重启 Docker）:"
  echo "   1. echo 'nameserver 223.5.5.5' | sudo tee /etc/resolver/<缺失域名>"
  echo "   2. sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder"
  echo "   3. 重启 Docker Desktop（必须！否则容器内 DNS 不生效）"
  echo ""
  echo "   或一键运行: sudo ./scripts/setup-dns.sh"
  exit 1
else
  echo ""
  echo -e "   ${GREEN}全部正常 ✅${NC}"
fi
